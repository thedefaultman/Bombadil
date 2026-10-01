# Bombadil's Brain

> **Status:** In progress. Pieces 1 and 2 have code that is not on `main`, and so has the search half of piece 3; pieces 4, 5 and 6 are designed and have no code.  
> **Code:** none of the six pieces is on `main`. They build on `src/bombadil/agentd.py` (the `read` list in each `turns.jsonl` row), `src/bombadil/procs.py` (the `bombadil-turn-*` scope a turn runs in), `src/bombadil/launcher.py`, `src/bombadil/apps.py` (`builtin_dir()`, where apps that ship with Bombadil are looked up) and `src/bombadil/browser.py` (Chromium's own profile). Where each piece stands: the list under "Build these first", the [roadmap](../roadmap.md) and [the brain](../architecture/brain.md).  
> **Design:** [Bombadil's Brain, the page](pages/bombadils-brain.html), [UX model](../ux/README.md), [Design system](../design-system/README.md)  
> **Decided by:** the project owner, 2026-09-27 (this brief, its recommendation and its 17 defaults)  
> **Verified:** 2026-10-01 against `main` at `26843d3`: that none of the six pieces is on `main`, the state of each piece below and the list "In the code today". What the unmerged work covers was read on its branch, not run. The kernel, browser and vendor facts under "Checked today" are dated 27 Sep 2026 and were not re-checked.

You have a PDF open, tap Super and click the chip that names it. The Brain rises around it: the PDF in the middle, and in fixed places around it where it came from (a page you read on Tuesday), what was made from it (a letter the machine drafted), what you used with it, and every time it changed, each line saying who and when. Zoom out and you see the whole machine as a map that holds still: Bombadil, Acme, Passwords, your lease, the sites you read, each a cluster of dots that are bright when they were touched lately and breathe while someone is touching them now, white for you, orange for the machine, blue for a coding session. Drag the strip along the bottom back to Tuesday and the map lights what you touched that day. Nobody writes any of it. The OS sees every save, download, turn and session edit as it happens, so the brain cannot fall behind, and every sentence a model writes about it is pinned to what it read and greys out when that changes. You learn one word, "brain", and the chip you already click.

This is the third experience brief. It sits on top of [How Bombadil should feel](ux-brief.md) and [Building on Bombadil](dev-brief.md) (both 27 Sep 2026, called the first and the second brief below), whose 29 and 19 decisions are confirmed and stay in force. Where the brain needs one of them extended, the change is named at the end. The project owner confirmed this brief, its recommendation (Focus, the Map and Time over one index) and its 17 defaults on 27 Sep 2026.

> **Reading note.** Sentences that say what the code does or what exists ("today", "Most of it does not exist yet", the list under "In the code today") describe the code on the day the brief was written, 27 Sep 2026. The list under "Build these first" says how far each of the six pieces stands on 2026-10-01. [Known issues](../known-issues.md) and the [roadmap](../roadmap.md) say what holds on `main`.

## Graph, or something better

The project owner asked for Obsidian's graph view, natively, and for something better if the graph is not the best experience. The [page](pages/bombadils-brain.html) draws four options side by side. The short version:

- **A. The Obsidian graph, drawn natively.** Every thing a dot, every link a line, a force layout. It is the best first minute there is: it looks like a brain, and clusters and orphans jump out. Used daily, it fails in four ways. At the size of a real machine (tens of thousands of files, pages, turns) it is a hairball. Positions reshuffle every time it opens, so there is no "where" to learn. A line has no reason and no date. And you cannot find anything by looking at it, so you search anyway. Obsidian's own users navigate with the local graph, links and search, and in 2025 Obsidian added Bases, table and card views of notes, beside the graph.
- **B. The Map (the overview I recommend).** The same dots and lines, but clusters are your areas (projects, apps, the folders you use, the sites you read), laid out once and pinned, so you learn it like a city. Only the few hundred most alive things are drawn, lines between areas are bundled into one road whose width says how much they share, and lines to single things appear only for the one you point at. A dot breathes while someone touches it, in that actor's color.
- **C. Focus (the default view I recommend).** One thing in the middle, a preview for a file or page and a list for a folder or project, with its links in fixed places around it: what it came from on the left, what it was read with on the right, what it is used with below, its changes on top. Each link is a short line saying why. Click a neighbor and it slides to the middle. This is Obsidian's local graph made readable, and because a folder in Focus is a list, it is also the file manager.
- **D. Time.** Lanes per area across the week, with each stretch of work drawn as a block named by the words you used. People remember "the thing from Tuesday, while I was reading about btrfs" far more often than a folder path. It becomes a strip under the other two views, and it replaces Rewind as a separate app.

**Recommendation:** Focus opens by default, the Map is the overview, and Time runs along the bottom of both. All three read one index. The free-floating force graph is not built as its own view, but the Map keeps its look, so the machine still feels like a brain when you zoom out.

## The rules

1. **Nobody writes the brain. The machine does, from what actually happened.** A save, a download, a turn, a session's edit, a page you read: every link is an event the OS witnessed, so there is nothing to tend and nothing to go stale.
2. **Every link says why and when.** "Downloaded from rent-portal while you read Lease renewal 2026, Tue 14:02." A link with no reason is a guess, and guesses are what turn a graph into noise.
3. **Plain files stay the truth.** Folders are still folders, every tool and both agents still read them, and the brain is an index beside them that can be rebuilt. Deleting it loses the view, not your data or its history.
4. **Things hold still.** A thing keeps its place on the map from the day it appears, through renames and moves, so you learn where things are the way you learn a city.
5. **Bright means alive.** Brightness is how recently and how often something was touched, by you or an agent. Nothing is removed for being old, it only dims, and the map at rest shows a few hundred things, never fifty thousand.
6. **What a model writes is pinned to what it read.** A summary carries a fingerprint of its sources. When a source changes, the sentence greys out and is rewritten the next time someone looks. This is the fix for the folder of markdown that goes stale.
7. **You and the agents see the same brain.** The pill, the Brain window, the machine's agent, coding sessions and apps all ask one index, so what the machine knows and what you can see never disagree.

## Build these first

Six pieces, in this order. The first one starts recording history, which cannot be recovered later, so it should start as early as possible. The next three are what gets used every day. The Map and Time come last because a map needs a few weeks of history to be worth looking at, and by then the first piece has collected it.

Where each piece stands, checked against `main` on 2026-10-01:

- **1. The brain writes itself: in progress, not on `main`.** `main` has two of its seams: every turn runs in its own `bombadil-turn-*` scope when systemd can make one (`src/bombadil/agentd.py`, `src/bombadil/procs.py`), and the turn's `turns.jsonl` row carries a `read` list (`src/bombadil/agentd.py`, `_log`). It has no `bombadil-brain` service, no watcher and no `brain.db`, and the row has no list of the files a turn wrote.
- **2. Focus: in progress, not on `main`.** `src/bombadil/apps.py` already looks up apps that ship with Bombadil (`builtin_dir()` is `share/apps`), but that folder does not exist on `main`, so there is no Brain app.
- **3. Find anything from the pill: in progress for the search, designed for the pill.** The index search is part of the unmerged work. `src/bombadil/launcher.py` on `main` matches app names, panels and a fixed list of commands, and offers no matches from an index.
- **4. The agents read the same brain: designed.** `src/bombadil/mcp_server.py` has no `brain_*` tool, and nothing on `main` puts a line from the brain into a prompt.
- **5. The Map: designed.** No code lays out areas or draws a map.
- **6. Time: designed.** No code groups events into stretches of work. Rewind, the app this piece folds in, is designed in the first brief and has no code either: on `main`, `history` opens `bombadil history`, a list of recent turns and launcher actions in a terminal (`src/bombadil/launcher.py`, `bin/bombadil`).

### 1. The brain writes itself

**What you see:** Nothing to learn and nothing to set up. Within a second of any save, download, turn or session edit, the thing and its links exist. The first visible payoff is two questions that answer instantly, with no model call. With the lease PDF in front, "where did this come from?" says "Downloaded from rent-portal on Tue 14:02 while you read 'Lease renewal 2026'." With ~/setup-wg.sh selected, "why is this here?" says "Made by the machine in turn 41, 'install the VPN', on Monday. Nothing has opened it since."

**Why first:** Every view, the search and the agents' tools read this index. More importantly, who made a file is known only at the moment it is written: a history that was not recorded cannot be rebuilt later, so every day without this piece is a day the brain will never know about.

**What changes:** A new user service, `bombadil-brain`, owns ~/.local/state/bombadil/brain.db (SQLite in WAL mode: things, links, events, and an FTS5 index), which the first brief already keeps outside home snapshots. It is fed by witnesses that report what happened, and it never scans on a timer.

- **Files.** A small root service, `bombadil-brain-watch`, holds one fanotify mark on the whole btrfs filesystem (a filesystem mark needs root, and it is the only kind that reports creates, renames and deletes with the writer's pid) and keeps the events under /home. It resolves each pid to who did it through its cgroup: a `bombadil-turn-*` scope (procs.py already runs every turn in one) is the machine's turn, a scope under bombadil-dev-<project>.slice is that coding session, an app's unit is that app, and anything else in your session is "you, in <app>". After a reboot or a stop it catches up with `btrfs subvolume find-new` for new writes and a quick walk of the tree against the last index for deletions and renames, so there is never a full rescan after the first index.
- **Turns.** agentd adds the files each turn touched (from its own tool events and the turn's snapper diff) to its turns.jsonl row, which today holds only the prompt, result and a details string.
- **Coding sessions.** The second brief's hooks (the prompt text from UserPromptSubmit) and its checkpoint refs (the files each session turn changed).
- **The browser.** Chromium's History database, copied when it changes and at most once a minute (Chromium keeps it locked): URL, title, time and how long you stayed. A download's page comes from the same database's downloads table, joined to the file the watcher saw Chromium write.
- **Apps.** The git history of each ~/Apps folder from the first brief, and app.toml.
- **The system.** pacman.log and each turn's snapper diff, as packages, services and /etc files, gathered into one System area.
- **What the agent learned.** Each line of memory.md as a fact, linked to the turn that wrote it.

Who made a file, and for a download where it came from, is also written on the file as xattrs (user.bombadil.made_by = "turn 41", user.bombadil.origin = the page), so it survives a move, a copy that keeps xattrs and a rebuild of brain.db. A thing's identity follows renames and moves, and an editor's save-by-rename keeps the same thing. What gets in is what a person would call a thing: caches, most dot-folders, git-ignored files, node_modules, target, .venv and the Trash stay out, a project is one thing with its tracked files inside, and an app is one thing with its data. The first index of a home runs at idle I/O priority and says so in one line ("Getting to know your files, 40%"); nothing waits for it.

**Effort:** L

### 2. Focus: the thing in front, and everything around it

**What you see:** Tap Super and click the "this" chip, or type "brain", and the Brain window rises on whatever "this" is. With snapshots.py in front, its first lines sit in the middle. On the left, where it came from: "builder on Bombadil, turn 12, 'drop the old cleanup', 13:02" and "flaky-snapshot, turn 3, 'fix the race', Tue". On the right, what it was read with: "Arch Wiki: Snapper, open while builder edited this". Below, what it is used with: "test_snapshots.py, changed in the same 5 turns", and in amber "launcher.py, also being changed by kit". On top, its changes: 7 this week as a small line, with Undo this change. Click any of them and it slides to the middle, with a trail of where you have been at the top. A folder or a project in the middle is a list (name, when, by whom), so the Brain is also where you browse files. Esc closes it.

**Why first:** Focus answers the three questions people ask about any file (where did it come from, what goes with it, who changed it), it stays readable however big the brain gets, and it gives the first brief's "why is this like this?" a screen.

**What changes:** A kit app, Brain, shipped in /usr/share/bombadil/apps like the other built-in apps. Focus has fixed slots per kind of link, at most five per slot with "+12 more", and a preview in the middle built from kit components (read-only Editor for text and code, an image, the first page of a PDF). It asks bombadil-brain over its socket (focus, search, map, timeline, subscribe). "This" becomes a thing: for the focused app, the files it has open under your home, read from /proc/<pid>/fd at the moment you tap Super, else the file it last saved; for the browser, the tab's URL; for a terminal, its shell's folder; for a coding session, the session. Clicking the chip opens Focus on it, and its x still sends your words without it.

**Effort:** M

### 3. Find anything from the pill

**What you see:** Type "lease" and the pill finishes it in grey: "lease-2026.pdf · Downloads · Tue". Tab walks up to three matches as chips (a file, a page, a stretch of work), and Enter opens the thing itself: the PDF in its viewer, the page in the browser, the stretch of work in Time. A sentence, such as "the pdf the landlord sent last month", goes to the agent, which asks the brain several ways and answers with the thing open and one line: "Opened lease-2026.pdf, downloaded 3 Sep from rent-portal." There is nothing new to learn, because typing a name already opens apps.

**Why first:** Finding is what people do most with files, and the pill is where they already are.

**What changes:** The launcher gets a second source of names: brain.search over names, titles and extracted text, answered from FTS5 with the trigram tokenizer in a few milliseconds and ranked by how alive a thing is. Only a match you accepted with Tab opens without the model, which is the first brief's rule that open never guesses. Extracted text is the first 64 KB of text files, PDF text through pdftotext, and image dates from EXIF.

**Effort:** S to M

### 4. The agents read the same brain

**What you see:** "what was I doing on Acme before the weekend?" answers in two lines and opens that stretch in Time. In a coding session on Acme, "use the approach from the zellij page I read last week" works: the session asks the brain and gets the three pages with their URLs, instead of asking you. When a session starts on a project, its first line of context says "Since your last session here: kit changed shell.qml, PR 418 merged, you read 3 pages about zellij."

**Why first:** Today every turn starts blind and spends tokens rediscovering the machine with find, ls and grep. An index the OS already keeps makes both agents faster and cheaper, and it makes what the agent knows the same as what you see.

**What changes:** os-mcp gains brain_search(query, kind, since), brain_focus(thing), brain_why(path), brain_timeline(since, area) and brain_recent. The bombadil-dev MCP from the second brief gains read-only brain_search and brain_focus scoped to the session's project, the pages linked to it and the machine turns it asked for, never your whole home. Results are plain text with paths and reasons, and page titles and URLs are marked as untrusted data, as the first brief's [Screen] block already is. The [Screen] block gains one line from the brain for "this" ("lease-2026.pdf, downloaded Tue from rent-portal"). The "since your last session here" line is built locally from the brain and added through the SessionStart hook, with no model call. Nothing else is added to a turn automatically; the agent calls the tools when it needs them.

**Effort:** S to M

### 5. The Map: the whole machine, holding still, showing what is alive

**What you see:** Type "map", or press the Map crumb at the top of Focus. Your areas sit where they always sit: Bombadil and Acme, Passwords and Tracker, Lease and Taxes 2026, Arch Wiki and GitHub, System. Each is a cluster of dots, bigger and brighter for what was touched lately and often. While builder edits snapshots.py its dot breathes blue; your save of the letter to your landlord pulses white; the machine installing qemu-full glows orange in System. Roads between areas are as wide as what they share, so Bombadil to Arch Wiki is thick and Lease to Bombadil does not exist. Point at a dot and its lines appear; click and it opens in Focus. Scrolling zooms into an area, where the dimmer things appear. "what's eating my disk?" switches the Map to size: the same places, with every dot and area sized by bytes instead.

**Why first:** It is the overview the project owner asked for, in the graph's look, but it can be learned like a city, it shows where work is happening right now, and it never becomes a hairball. It comes fifth because an empty brain draws an empty map.

**What changes:** bombadil-brain lays areas out once with a force layout over the links between areas, places each thing around its area in a spiral ordered by when it arrived, and stores every position in brain.db. A new thing takes the nearest free spot to its area, and an area that grows makes room by moving only its newest members. The Brain app draws the Map in one scene-graph item (a few hundred dots and a dozen roads), and the live pulses come from the same event stream the witnesses feed. What is drawn at rest is chosen by aliveness (recency, frequency, and whether you or an agent touched it), with areas always shown.

**Effort:** M

### 6. Time: the week as stretches of work, and every version one drag away

**What you see:** A strip along the bottom of the Brain holds the last seven days. Each block is a stretch of work, named by the words you used ("install the VPN", "fix the race") or by its main thing ("Lease renewal 2026"), and placed in its area's lane. Hovering a block shows what it touched and the first brief's thumbnail of the screen. Dragging the handle back to Tuesday lights on the Map what was touched then and dims everything else. In Focus, dragging steps the thing through its versions, and "Go back to this" restores one. "history" and Super+Z still open it, at now.

**Why first:** People remember by when and by what else was going on far more than by where something is saved, and without this the first brief's Rewind and the brain would be two history views.

**What changes:** bombadil-brain groups events into stretches locally, splitting at 20 minutes of quiet or a change of main area, and names each from the turn's words or the most-touched title, with no model. Versions come from git for apps and projects (including the second brief's checkpoint refs) and from snapper for home and /etc once home has its own config, as the first brief planned. Rewind's data (turns.jsonl and the thumbnails) is read as it is.

**Effort:** M

**Next, after these six: Files through the Brain.** Once Focus on a folder can move, rename, copy, trash and take a drag, "files" opens the Brain on your home and the Files panel retires. File operations made in the Brain undo with Ctrl+Z inside it, like any file manager, and "tidy my downloads" through the pill is an ordinary turn with its receipt and Undo. Nautilus stays installed for the rare case and as the file chooser behind Chromium's uploads.

## A day with the brain

This is how a day should feel once the six pieces and the best of the rest are built. Most of it does not exist yet.

**Finding.** In the middle of the morning, Super, "lease". The pill finishes it with "lease-2026.pdf · Downloads · Tue". Enter, and the PDF opens.

**Where it came from.** With the PDF in front, a click on the chip. Focus shows it came from rent-portal's "Lease renewal 2026" page on Tuesday at 14:02, that the machine made letter-to-landlord.odt from it in turn 57 ("draft a reply saying we'll renew"), and that budget-2026.ods was open beside it both times.

**A file you do not recognize.** ~/setup-wg.sh sits in your home. "why is this here?" answers "Made by the machine in turn 41, 'install the VPN', on Monday. Nothing has opened it since." with Move to Trash.

**At the code.** You are reading snapshots.py in reviewer. A click on the chip shows that builder rewrote prune() an hour ago, flaky-snapshot touched it on Tuesday, and kit is changing launcher.py, which changed with it five times. "tell flaky-snapshot builder rewrote prune() an hour ago" reaches the session as typed text.

**A session that asks instead of guessing.** In Acme's Claude Code: "use the approach from the zellij page I read last week". The session calls brain_search and continues with the right URL.

**Friday.** "history", then drag back to Tuesday afternoon. The Map lights Lease, rent-portal, and flaky-snapshot's corner of Bombadil. You click the block named "fix the race" and see the four files it changed.

**Tidying.** "tidy my downloads" is a turn. On the Map, dots leave Downloads for Lease, Taxes 2026 and Installers as it works, keeping their links, and the closing line says "Moved 23 files into 4 folders. [Undo]".

**Evening.** "map". Acme is dim after a quiet week, Bombadil is bright, and a new area, Wedding, has appeared since Wednesday from a folder and a few pages. "make me a reading list of what I read about btrfs this month" builds a small app that asks the brain, so it stays current without anyone updating it.

## What the brain is made of

**Things:** files and folders, projects, apps, coding sessions, the machine's turns, pages and sites, downloads, packages, services and /etc files, facts from memory.md, and stretches of work.

**Areas:** a project in ~/Projects, an app in ~/Apps, a folder in your home you use (Lease, Taxes 2026), a site you read often, and System. Areas come from where things live and where you go, never from tags you maintain.

**Links, and the event behind each:**

| Link | Made when | Strength |
|---|---|---|
| made by, changed by | a write, with the writer's scope; a turn's tool call; a session checkpoint | strong |
| came from | a download's origin xattr; a turn that read X and wrote Y | strong |
| lives in | the folder, project or app a thing is in | strong |
| asked about | a turn whose "this" was the thing | strong |
| mentions | a turn's words naming it; a URL or a [[link]] in your own file | strong |
| used with | changed in the same turn, or open in the same stretch of work, at least twice | weak, shown only in Focus |
| read with | a page open while the thing was being changed | weak, shown only in Focus |

Every link keeps when it first and last happened, how many times, and the events behind it, so "why" is always a click away.

**Where the model comes in:** names come from folders, projects, page titles and your own words, with no model. A one-line description of a thing is written only when someone looks at it and it has none, by the fast model, pinned to a fingerprint of what it read, and greyed when that changes. Private things never get one.

## Decisions I picked a default for

Each of these forks. I picked a default and say why; the other options are listed for the record. The project owner accepted every default on 2026-09-27, so these are confirmed decisions, not proposals.

**Is the main view a graph?** Default: Focus opens by default, the Map is the overview, and Time runs along the bottom. The Map keeps the graph's look with pinned positions, a few hundred alive things and bundled roads; a free-floating force graph of everything is not built. The global graph is lovely for a minute and unusable after a month, while the local graph is what people actually navigate with. Other options: Obsidian's global graph as the home view; a 3D brain; a dashboard of cards.

**Is the brain the file system?** Default: no new file system. Plain folders on btrfs stay the truth, and the brain is an index beside them. The Brain becomes the file manager once Focus on a folder can move, rename, trash and take a drag, and Nautilus stays as a guest and as the upload file chooser. A database-backed file system with tags instead of folders would break every tool and both agents, which read plain paths. Other options: a FUSE file system organized by the brain; keeping Nautilus as Files for good.

**Who makes links?** Default: only witnessed events. The model never invents a link. The agent adds one only when you ask ("link this to the lease"), recorded as yours, and [[links]] and URLs in your own files count as links you wrote. Links guessed from similar content are the main source of noise in a graph and are wrong often enough to make every line suspect. Other options: "similar" links drawn from embeddings; links only from files you write.

**Search: words or meaning?** Default: a word index (FTS5 with trigrams over names, titles, extracted text and your prompts), with the agent as the layer that understands meaning: it rephrases and asks the index several times. No embedding model ships for now. The agent already understands "the pdf the landlord sent", and a local embedding model costs disk, memory and CPU on every write for a gain that has not been shown yet. Other options: a local embedding model (revisit if sentence searches miss); a provider's embeddings (the CLIs have none, and it sends your files away).

**Reuse GNOME's LocalSearch or KDE's Baloo instead of a new index?** Default: no, a new index of our own, with their extractors borrowed where useful (pdftotext for PDFs). Both index file contents by crawling folders; neither knows who wrote a file, which turn made it, which page it came from or what was open beside it, and those links are the whole point. Other options: LocalSearch as the file index with the brain on top (two indexes to keep in step, and a crawler beside a watcher); Baloo.

**How much of a file does it read?** Default: names, metadata and the first 64 KB of text, PDF text, and the date and camera from EXIF but not the location. Contents of private apps, ~/.ssh, ~/.gnupg, keyrings and password files are never read, only their names. Office documents come later. Other options: everything (slow first index, secrets in the index); names only (search misses the lease).

**How much of the web does it keep?** Default: URL, title, time and how long you stayed, from Chromium's History. Page text only for pages an agent read in a turn or that you asked it to remember. Incognito never, because Chromium keeps no history for it. A full-text record of everything you read, on a machine where an agent has sudo, is more than most people would want kept. Other options: the text of every page; no web at all (then "came from" dies).

**When does a model write about your things?** Default: only when someone looks and there is nothing yet, using the fast model, pinned to a fingerprint and greyed when it goes out of date; never a background crawl, and never for private things. Nightly summaries of everything would spend tokens and be stale by the morning anyway. Other options: summarize everything nightly; no summaries at all.

**Colour: who or what?** Default: position says what (the area), colour says who touched it: white for you, orange for the machine's agent, blue for coding sessions. Areas are told apart by their place and name. Three colours can be read at a glance; a colour per area stops working at nine areas. Other options: a colour per area; a colour per kind of thing.

**How many things does the Map draw?** Default: about 200 at rest, picked by aliveness, with every area shown; zooming into an area reveals its dimmer things. Other options: everything (the hairball); areas only.

**Where does the Brain open?** Default: on "this" in Focus; "map", or "brain" with nothing in front, opens the Map; "history" opens Time at now. It is a stage window, large and central like an app, because you work in it. Other options: always the Map; a panel that slides in and leaves on a click.

**Does Rewind stay its own app?** Default: no. It becomes the Brain's time strip, and "history" and Super+Z open it, so there is one place for what happened and what is where. Other options: Rewind beside the Brain.

**What does undo do to the brain?** Default: nothing, because brain.db is outside home snapshots. An undo is recorded as an event, and the things it touched step back to their earlier versions in the brain, which then shows "undone by you". Other options: snapshot the brain with home (then undo would erase the record of what was undone).

**Where do facts about you live?** Default: in memory.md, as confirmed. The Brain shows each line as a fact linked to the turn that added it, and marks it "source gone" when the file or project it names no longer exists. The brain never writes project knowledge into memory.md, because every coding session loads it. Other options: moving memory into brain.db (both CLIs read memory.md as their instructions file, so it stays).

**What does the brain keep of coding sessions?** Default: your words from each prompt, the files each session turn changed, and when the session started, waited and ended, all from the second brief's hooks and checkpoints. Transcripts stay in the vendors' own files, which the second brief rules out reading. Other options: transcripts in the brain.

**How much of the disk does it cover?** Default: your home, plus a System area built from pacman and turn diffs (packages, services, and the /etc files that changed), not every file under /usr. External drives appear as an area while they are mounted, with names only. Other options: the whole disk; home only.

**Is anything added to every turn?** Default: one line for "this", nothing more. The agent calls brain tools when it needs them. More context on every turn slows the first words, which the first brief puts a 200 ms rule on. Other options: a digest of recent activity in every prompt.

## Everything else, by moment

### Finding

- **Words for time and kind.** "pdfs from last month", "what did I download yesterday" and "pages about zellij" work in the pill's Tab matches through a small fixed grammar (a kind word and a time word), and anything longer goes to the agent.
- **Drop onto the pill.** The first brief's drag-and-drop chip gets the brain's line: dropping a file shows "lease-2026.pdf, from rent-portal" as its chip.
- **The file chooser.** Chromium's upload dialog is the one place people search for files outside the pill. A portal backend that lists "recent in the brain" first comes after Files through the Brain.

### Understanding

- **Why is this like this?** The first brief's idea gets Focus: "why is my DNS set to 1.1.1.1?" opens /etc/resolv.conf in Focus with the turn that changed it on the left and "Undo just this file" on top.
- **Pin and forget.** Pinning keeps a thing bright on the Map whatever its age. "forget this" removes a thing and its links from the brain and keeps it out, and the file itself stays.
- **Your own notes.** Notes you write are ordinary files. [[links]] and URLs in them become links, so an Obsidian vault copied into your home shows up as an area with its own links, next to everything else.

### Working

- **The project in Focus.** A project in the middle shows its sessions with their dots, the files changed this week by whom, the pages read while working on it, the packages it asked the machine for, open pull requests, and overlap between sessions. The second brief's project card gets an "Open in Brain" button.
- **A map of the code (later).** For a project, modules as dots and imports as lines, sized by lines of code and lit by recent change, coloured by who changed them. This is the one place a free graph earns its keep, because code really is a network.
- **Apps on the brain.** The kit gets a Brain object with search, focus and subscribe, so "make me a reading list" or "show me what my sessions did this week" is an ordinary app that stays current by itself. An app can describe its own items in app.toml (Tracker's runs, Passwords' entries by name only) so search and Focus can show them.

### When it goes wrong

- **The index is a cache.** Deleting brain.db rebuilds it from files, xattrs, git, turns.jsonl, snapper and History; only the weak "used with" links from before are lost. `bombadil brain rebuild` does it on purpose.
- **The brain is never in the way.** If bombadil-brain dies, the pill, turns, undo and apps carry on; its witnesses spool to a file and it catches up when it returns, as the second brief does for agentd's signals.
- **A full disk or a huge home.** The first index runs at idle priority and pauses on battery; a folder of a million generated files becomes one thing ("data, 1.2 M files") rather than a million.

## Cut, and why

- **A free-floating global graph as the home view.** A hairball at real size, no "where", no "when", and no reasons on the lines.
- **"Similar" links from embeddings drawn on the Map.** Noise that looks like knowledge, and wrong often enough to make every line suspect.
- **A wiki or notes folder the agent keeps.** Exactly the stale markdown the project owner ruled out; prose that is not pinned to its sources rots.
- **Tags you maintain.** The same chore as notes. Areas and stretches of work come from what happened.
- **A virtual file system in place of folders.** Every tool and both agents read plain paths.
- **A 3D brain.** Harder to read, harder to click, and slower in a VM.
- **Background summaries of everything.** Tokens spent on things nobody looked at, stale by the next morning.
- **Screenshots every minute to see what you are doing.** The first brief already rejected it; windows, files and URLs say more.

## Gaps worth thinking about

### In the code today

Checked against `main` on 2026-10-01: the first three gaps still hold. The turn row carries a `read` list but still has no list of the files a turn wrote (`src/bombadil/agentd.py`); the installer creates a snapper config for `/` only (`iso/airootfs/usr/local/bin/bombadil-install`); and an app is found by its process name, `bombadil-app run <name>` (`src/bombadil/procs.py`). The fourth is closed: `src/bombadil/browser.py` starts Chromium with its own `--user-data-dir`.

- A turns.jsonl row has no list of the files the turn touched, only the prompt, the result and a details string, so "made by turn 41" needs agentd to record them.
- There is no snapper config for /home yet, so versions of home files wait for it, as the first brief's live undo does.
- Generated apps are started by `bombadil-app run` rather than each in its own systemd user unit, which the first brief planned, so writes by apps are attributed by process name until that lands.
- Chromium still starts without its own --user-data-dir, and the History path has to be fixed along with it.

### Not designed yet

- The brain from the phone. The Claude app reaches coding sessions, but there is no way to search the brain or open Focus away from the desk.
- More than one machine, and shared folders synced from elsewhere.
- Email, calendar and people. Bombadil has no mail client yet, and people are the most natural things to link.
- Sharing a view: sending someone "the lease, and everything around it".
- How the brain feels with a million files and five years of history: aliveness weights, archiving old areas, and whether the Map needs seasons.

## What would make it Obsidian with a new skin, or another stale folder

- A graph that reshuffles every time it opens.
- Every file drawn as a dot.
- Lines without a reason, or links a model guessed.
- A folder of summaries the agent writes and nobody reads.
- A notes app with a new name.
- Tags to maintain.
- A file tree in a sidebar beside the graph.
- A history app and a brain app that disagree about what happened.
- Summaries with no date that are silently wrong.
- A spinner that says "indexing" before you can do anything.

## Extends or changes the first two briefs

- **Rule 8, one memory you can read:** extended. The brain is the machine's readable memory of what happened; memory.md stays the short list about the person.
- **Rewind:** folded into the Brain as its time strip. "history" and Super+Z are unchanged; Rewind's thumbnails and rows are read as they are.
- **Why is this like this?:** gets Focus and brain_why, reading the brain instead of turns.jsonl alone.
- **What skips the model:** extended with Tab-accepted matches from the brain (files, pages, stretches of work), "brain", "map", "where did this come from?", "why is this here?" and "forget this". Enter on text that was not accepted still goes to the agent.
- **"This":** extended. It resolves to a thing in the brain, open files come from /proc/<pid>/fd of the focused app, and clicking the chip opens Focus on it.
- **The Files panel:** replaced by the Brain once its folder view can move, rename, trash and take a drag; the Brain is a stage window, not a panel. Nautilus stays as a guest and as the upload chooser.
- **The [Screen] block:** gains one line from the brain for "this", marked untrusted like the rest.
- **Home snapshot exclusions:** unchanged; brain.db is under ~/.local/state/bombadil, which is already excluded.
- **Apps as systemd user units:** now also needed for attribution, so writes by an app are named by the app.
- **The project card (second brief):** gains "Open in Brain".
- **The bombadil-dev MCP (second brief):** gains read-only, project-scoped brain_search and brain_focus; sessions still never get os-mcp.
- **SessionStart context (second brief):** gains one "since your last session here" line, built locally.
- **Reading vendor transcripts (cut in the second brief):** stays cut; the brain uses hooks and checkpoints only.

## Checked today

These are the facts the design rests on, checked against kernel, browser and vendor sources on 27 Sep 2026. A builder should re-check the ones marked as moving.

- fanotify: an unprivileged process gets only inode marks with no other process's pid, so the watcher is a root service. A filesystem mark (`FAN_MARK_FILESYSTEM`, needs CAP_SYS_ADMIN) reports creates, deletes, renames and moves with the writer's pid, and `FAN_REPORT_PIDFD` (since 5.15) gives a pidfd for a safe lookup of its cgroup. Directory-entry events still fail on mount marks in mainline, so it must be a filesystem mark.
- fanotify on btrfs: a filesystem mark with FID reporting placed on a subvolume such as /home fails with EXDEV, because btrfs mixes the subvolume id into the fsid, and the 6.8 change only allowed inode marks inside one subvolume. The way through, read from the source and not yet run: mount the top-level subvolume (subvolid=5) at a private path and mark that; every subvolume shares the one superblock, so /home is covered. Moving.
- `btrfs subvolume find-new` lists files written at or after a generation and prints the next marker, but it misses deletions, renames and metadata-only changes and needs root, so catch-up after a stop pairs it with a walk against the last index. There is no kernel change journal; `btrfs send --no-data -p` between two read-only snapshots is the full diff once home has snapshots.
- Chromium on Linux no longer writes user.xdg.origin.url and user.xdg.referrer.url on downloads (the code left in 2019). Where a download came from is in the History database's downloads table (target path, referrer, tab URL). The brain writes its own xattrs.
- Chromium's History database has a visit_duration column on visits, and Chromium holds the file with an exclusive lock, so the brain copies it rather than reading it in place.
- Claude Code hooks: PostToolUse for Write and Edit carries tool_input.file_path, UserPromptSubmit carries the prompt, and there is a FileChanged event. Codex hooks exist (SessionStart, SessionEnd, PreToolUse, PostToolUse, UserPromptSubmit with the prompt, Stop, Interrupt, PermissionRequest and more), but its edits arrive as patch text with no file path, which is why the files a session turn changed come from the second brief's git checkpoints on both vendors.
- Obsidian's graph view has a global graph, a local graph with a depth slider, filters, colour groups by search and a time-lapse by creation date. Obsidian added Bases (table, cards, list and map views of notes) in 2025, with kanban in September 2026, so even Obsidian now navigates notes as tables beside the graph.
- Qt Graphs (6.11) draws charts only; a node-link map needs a custom QQuickItem or Qt Quick Shapes with our own layout, which is what piece 5 says.
- GNOME's LocalSearch (the renamed tracker-miners, Arch package `localsearch`) and KDE's Baloo both index file contents by crawling and expose search over D-Bus; LocalSearch extracts PDF text with poppler. Neither records who wrote a file or what it came from.
- SQLite ships FTS5 with the trigram tokenizer since 3.34 (Arch builds it in); searches under three characters match nothing, so the pill falls back to name prefixes for one and two letters. sqlite-vec is AUR and pip only and pre-1.0, which is one more reason to leave embeddings out for now.

**Unverified, and to test in the VM before piece 1:** the fanotify mark on a subvolid=5 mount catching writes made through /home and ~/Projects; whether the watcher keeps up with a large build's write rate without dropping events (a queue overflow event would force a walk); resolving a pidfd to a cgroup fast enough for a burst of thousands of events; and that xattrs on downloads survive Chromium's rename from the .crdownload file.

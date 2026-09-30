# Riding with Bombadil

You ask for a VPN that starts at login and lean back. The line above the pill says "Writing the tunnel settings", and when you rest the mouse on it a quieter line underneath says why, in the machine's own words from a second ago: "your router hands out 192.168.1.x, so the tunnel uses 10.8.0.x". The step that changes /etc has its amber edge and its command, and under the command, in muted amber, "after reading wireguard.com/quickstart", so you can see where the idea came from while Esc is still in reach. When it is done, the closing line comes with a small picture that the machine drew from itself, not from the model: this laptop, Wi-Fi "Home", the router, a new orange box for the tunnel, the internet, and Claude at the end of the chain, with the part that changed lit. Later you say "tell me when the ISO is downloaded" and a small chip flies from the closing line into the dot, where it stays until it comes true, even across a restart. At no point did you ask for an explanation, read a paragraph, open a terminal or approve anything. You rode along and could see the road.

This is the fifth design pass. Daniel asked what else an AI-native OS needs if it is for the AI and the user is a passenger who gets things explained visually. It sits on four confirmed briefs (How Bombadil should feel, Building on Bombadil, Bombadil's Brain and The Bombadil Desk), whose decisions stay in force; where a piece here needs one of them extended, the change is named at the end. Four things are owned by other threads and only named here: the desk's widgets, Bombadil's voice and its welcome and empty states, the self-improvement loop (repeated asks turned into widgets or apps, Bombadil finding and fixing its own bugs, growing its kit), and the carry-away copy of the project.

I looked at the machine's whole life through one question: does the passenger see what is happening and why, without asking? Nine slices were searched (watching it work, explaining, the agent's mind, what runs on its own, the machine itself, keeping it current, trust, more than one machine, and OS basics). The first six produced 50 gaps, each checked by its finder against the repo and vendor sources. The usage limit stopped the last three slices and the independent second check, so the trust, other-machine and basics items below are mine, marked where a fact is not checked.

## The rules

1. **It explains while it drives, in its own words from that moment.** A reason is the sentence the agent wrote just before acting, kept instead of thrown away. Nothing is explained afterwards by a second model, which would cost time on every turn and could disagree with the first.
2. **Pictures of the machine come from the machine.** The OS captures the network, the disks, the boot, a service, the sound and the screens from real commands and draws them. The AI picks what to point at and adds one line. A model never hand-draws the state of your machine, because a passenger cannot check it.
3. **Records answer before models.** "Why?", "what changed?", "what are you holding?" and "what runs on its own?" are answered first from what the machine recorded, instantly and offline. The model is asked only when nothing local knows.
4. **Outside words are marked by order, not guessed by cause.** A change that comes after the machine read a web page, a downloaded file or a request from a session says "after reading github.com". The order is known; the cause is not.
5. **Nothing runs on its own that is not in one list.** Every promise, routine, watcher, job and piece of upkeep is a systemd unit you can see in Autopilot and stop. A promise that is not a unit does not exist, and the agent is not allowed to make one in words alone.
6. **Upkeep is prepared quietly and done in the open.** Checks and downloads happen in the background. Installing and fixing are turns you can watch and undo, started by your word or by a schedule you gave in words.
7. **Nothing new to learn.** Every piece is a word in the pill, a hover, or a question mark on something already on screen: the line, the dot, the cards, the desk, the Brain.

## Build these first

Six pieces, in order. The first two are the explaining layer: every later piece, the desk's Now and the brain's "came from" links read what they record, and they are what a build thread should take first. Three and four make the machine's hands and its promises visible. Five and six cover what it knows and keeping it current.

### 1. Why, and after reading what

**What you see:** While a turn runs, hovering the line shows a quieter second line with the reason for the current step, in the agent's own words from just before it acted: "NetworkManager owns DNS here, so changing its settings instead of resolv.conf." A step that changes the system keeps its amber or red edge and its exact command, and if anything from outside was read earlier in the turn (a web page, a downloaded file, the page in the "this" chip, a request from a coding session or an app), a third line under the command says "after reading wireguard.com/quickstart". Typing "why" into the pill during a turn flashes that reason at once instead of queueing a grey chip. The desk's Now card gets a route to draw, and each stop on it carries its reason. After the turn, Details lists every step with its reason, and everything the turn read, each marked yours or outside.

**Why first:** With no permission prompts, seeing why before a change lands, and where the idea came from, is the passenger's whole safety model. Today the sentence before each step is thrown away the moment the step starts (narrate.py clears it at every tool start), and the desk's Now would stay empty on Daniel's machine, because both vendors now ship their plan tools switched off. It costs no model call, and every later piece reads what it records.

**What changes:**

- **Turn the plan tools on.** Claude Code offers TaskCreate, TaskUpdate and TodoWrite by default only on older models (Claude 3.x, Opus 4 to 4.7, Sonnet 4 to 4.6, Haiku 4.5); on newer ones they are left out unless CLAUDE_CODE_ENABLE_TODO_TOOLS=1 is in the CLI's environment. Codex registers update_plan only with `[tools.update_plan] enabled = true`. providers.py sets the variable for Claude and passes `-c tools.update_plan.enabled=true` on Codex's fresh and resume commands, and Codex.parse stops faking a one-item TodoWrite and passes the whole list. agentd ships the `plan` event exactly as the desk brief specifies (the whole table per update, stamped with the turn); whichever of this piece and the desk's Now lands first builds it and the other reuses it. The line gains " · 2 of 4".
- **Keep the reason.** narrate.Narrator keeps the last sentence of what the agent said when a tool starts, and attaches it to the status as `because` (at most 140 characters, with filler openers such as "Let me", "I'll", "Now" and "Great" dropped). Bash's own `description` is the fallback. On Codex, an agent_message completed just before a command, MCP call or file change is the reason. Thinking text is never a source.
- **Keep what was read.** The Narrator keeps a per-turn `read` list: Read and cat or less on files, WebFetch and WebSearch, curl and wget hosts (narrate already extracts them, never credentials), Codex web_search items, later the browser tools of piece 3, the [Screen] block, prompts marked "[asked by coding session …, untrusted]", and app requests. Each is classed yours or outside; outside means web pages, files whose origin xattr names a page, the [Screen] page and selection, and anything a session or app asked. On a system or irreversible step after an outside read, the status gets `after: {label, kind}` naming the latest one. turn_end carries `read`, and agentd writes it into turns.jsonl, where the brain's "came from: a turn that read X and wrote Y" witness picks it up.
- **Show it.** StatusLine draws `because` as a faint second line on hover (always under the command on marked steps) and `after` in muted amber under that; the hover pop-out joins the bar's input mask. launcher.py answers a bare "why" or "why?" during a turn from the current step's reason; at any other time it goes to the agent as today.
- **Ask for it.** Two clauses in the system prompt: "When a request takes three or more steps, write the plan first with your task tool, in short plain words the user will read (no file names, commands or tool names), and keep it updated." and "Before each step that changes the machine, say in one short plain sentence why: the reason, not the action."
- **Test it.** Stream fixtures for both providers with a plan and reasons; rule-table tests for filler and for outside sources next to test_narrate.py.

**Effort:** S to M

### 2. Pictures from the machine

**What you see:** "how am I connected?" draws your real path at once: this laptop, Wi-Fi "Home" at 82%, the router at 192.168.1.1, the internet, and Claude as the last hop, with a note under the router saying "names are looked up at 1.1.1.1". When something is wrong the first broken link is red with one sentence under it ("Your Wi-Fi is fine, but the router isn't reaching the internet"), and because nothing in it asks the AI, it works offline. "what starts when I boot?", "where did my disk go?", "what's playing where?" and "what does bluetooth need?" draw the boot chain with the slow step in amber, the disks, the sound (which app plays to which speaker) and a service with what it needs. When a turn changes one of these parts, its closing line comes with a small before and after: names looked up at 192.168.1.1 before, 1.1.1.1 after, the change lit orange. When the answer is about ideas rather than this machine ("how does a VPN work?"), the agent fills in the same kind of card with boxes and lines, and on Claude the boxes appear while it is still writing them. Every box opens the thing it names. A picture never holds more than about a dozen boxes; anything bigger opens as a window.

**Why first:** This is the direct answer to "explained in a visual way". Most explanations of a computer have parts, order or change, which is what a line of text is worst at. And a passenger cannot check what the AI says about the machine, so pictures of the machine must come from the machine: captured in a fraction of a second, costing no tokens, identical on Claude and Codex, and never made up.

**What changes:**

- **The card host and show_card.** The first brief designs typed cards drawn by the shell (piece 5), and nothing builds them yet: show_card exists only as a name in narrate.py. This piece builds the shell's card host and its first kind, `diagram`; list, checklist, timer, control and the markdown reader follow in the first brief's piece 5 as planned.
- **The diagram card.** Input: a shape (chain, layers, compare or timeline), a title, up to 12 nodes (a label of at most 32 characters, a sub-line, a state of ok, warn, bad, new, gone or active, a kit icon, a side for compare, a time for timeline, and the thing it opens: a path, unit, package, URL or turn), up to 16 labelled links, a highlight and one sentence. os-mcp validates it and returns fixable errors the way create_app does. Layout is fixed and never a force layout: "layers" is ranked in os-mcp (about 100 lines of Python), and the other three are plain QML positioners.
- **One Diagram.qml in the kit** (share/qml/Bombadil, PR #2), used by the card host, by agent-written QML cards and by apps, so every picture in the OS looks the same. Boxes are Rectangle and Text on Theme. All links are one Shape with a ShapePath each and the CurveRenderer, not Canvas, which in Qt 6 uploads a texture on every update. A scratch benchmark built and drew 12 boxes and 12 links in 7 to 8 ms warm.
- **Streaming.** agentd already sees Claude's tool input as it streams (partial_step reads create_app's title that way), so it forwards each finished node and the frame and first boxes appear while the rest is written. Codex sends the call whole.
- **Six parts of the machine, captured.** A new sysmap.py holds pure capture functions that return diagram data: network (`ip -j route get`, `ip -j addr`, `nmcli -t` for the Wi-Fi name, signal and DNS, resolv.conf, NetworkManager's connectivity check and a probe of the active provider's own host, drawn last under its brand), boot (`systemd-analyze critical-chain` parsed from text, `plot --json=short` for a timeline), one service (`systemctl show -p Requires,Wants,After,ActiveState,SubState,FragmentPath` and the package that owns it), disks (`lsblk -J`, `findmnt -J`, btrfs subvolumes), sound (`pw-dump`) and screens (`hyprctl -j monitors`). Each capture runs in a thread with a 500 ms budget.
- **A new os-mcp tool, system_map(kind, target, highlight, say),** captures, shows the card itself and returns a short text version, so the model writes about 30 tokens instead of the picture.
- **Receipts.** At turn_start agentd captures, without waiting on it, the kinds the turn is likely to touch (narrate already classifies network, service, package, sound and screen steps), captures them again at turn_end, stores both beside the turn's event log, and when they differ the closing line gets a compare card. The network capture is the same probe the confirmed "puts back a change that cut it off" check runs, so it is taken once and used twice.
- **One clause in the system prompt:** "When an answer has parts, order or change, show it as a picture (system_map for this machine, a diagram card otherwise), then say one line."

**Effort:** M to L (the card host is most of the extra)

### 3. The machine's hands in the browser, and taking the wheel

**What you see:** When the machine uses the web for you, the browser slides in with a thin orange frame around the page, which means its hands are on it. An orange pointer glides to each thing it clicks with a small label ("Click: Generate token"), and what it types appears in the field, passwords as dots. The line says the same in words ("Filling the search box on github.com"). Move the mouse onto the page or click, and the frame fades and the line says "You have the browser": the machine stops touching it, finishes whatever else it was doing, and ends with one line such as "Sign in, then say continue." You do your part, say "continue" or press Continue, and it picks up from where you are, knowing which pages you went to. At a sign-in, a code sent to your phone or a picture puzzle, it hands you the wheel the same way instead of guessing.

**Why this early:** Driving Chromium is a foundation choice and the first brief lists it under "Not designed yet" ("The agent driving the browser while you watch. The panel slides in, a highlight shows what it clicks and types, you grab the mouse to take over, and you say 'continue' to hand control back."), yet os-mcp has no browser tools at all today. Of everything the machine does for a passenger, acting on the web in their accounts is what most needs to be seen.

**What changes:** os-mcp gains a small browser tool set (open, look as an accessibility snapshot with refs, click, type, scroll, read_text) on Python Playwright's `connect_over_cdp` to the panel's Chromium, which first needs its own --user-data-dir (an open code gap: Chromium 136 and later ignore the debugging port on the default profile). With Playwright 1.59 or later each driven page gets `screencast.show_actions` for the moving pointer and label and `show_overlay` for the orange frame. The agent works in its own tab unless the ask is about the "this" page. Takeover: while a turn drives the browser, os-mcp watches the cursor over Hyprland's socket against the browser window's geometry and listens for pointer and key events in the page that arrive while no agent action is in flight; either one ends the machine's browser use for the rest of the turn, and later browser calls return "The user took the browser at <url>; end with one short line saying what is left." "continue" is an ordinary turn whose prompt is prefixed with what you did while you had it (pages and the names of what you clicked, never what you typed). narrate gets lines for the browser tools, and the system prompt one rule: hand over at sign-ins, 2FA and CAPTCHAs, and never navigate away from the user's own tab.

**Effort:** M to L

### 4. Promises that keep, and one Autopilot list

**What you see:** When you say "tell me when the ISO download is done", or the agent itself says it will let you know, a small chip appears on the closing line ("When the ISO finishes: tell you") and slides into the dot, so you see where the promise went. When it comes true, one line appears in the pill, as decided. Promises survive a restart; one missed while the laptop was off speaks once when you log in and says it is late. "every night, clear old installers out of Downloads" runs right away while you watch, then says "I'll do this every night" with a clock mark and a Stop; later runs happen only while you are away, each with its own restore point, and show up in the desk's Away card with Undo. Type "autopilot" (or the decided "what are you watching?") and one card lists everything that runs on its own in four groups (routines, watchers, jobs running now, and the machine's own care), each row with when it runs next, how the last run went, whether it asks the AI each time, and Pause and Stop. "Hold everything" pauses routines and care for a flight or a day of presentations.

**Why:** A passenger has to be able to trust "I'll let you know". Today nothing on main schedules anything, and the vendors' own schedulers in a `claude -p` run either die with the process, are cleared by a fresh session, or keep the turn open. A promise that silently dies is worse than no promise, and background work you cannot list is how a machine starts to feel like something you maintain.

**What changes:**

- **Real units, one record.** New os-mcp tools promise(when, say, run?), set_routine(name, when, words, recipe) and set_watcher(name, trigger, check, words, needs_model) write persistent unit files under ~/.config/systemd/user named `bombadil-{promise,routine,watch}-<slug>`, with an `[X-Bombadil]` section holding your words, the turn that made it and whether it uses the model (systemd ignores X- sections, so the unit files are the registry). Calendar timers get Persistent=true, which works only with OnCalendar; transient systemd-run timers live in the runtime directory and vanish at reboot, which is why these are files. Because the units live in home, undoing the turn that made one takes it back.
- **Firing.** `bombadil promise|routine|watch fire <slug>` sends one message to agentd's socket (spooled while agentd is down). A promise speaks through the pill line, with no model and no turn. A routine or a watcher marked needs_model is queued as an ordinary turn tagged with its origin, only while you are away (the second brief's presence file) unless you named an exact time, one at a time, and waiting out a provider limit. Two failed runs in a row disable it and one line offers Fix and Stop.
- **Checks without the AI.** Whenever it can, the agent compiles a watcher into a short check script (run with NoNewPrivileges and a 30 s timeout) or a .path unit, and a routine without judgment into a script after its first run, so "tell me when a file lands in Downloads" costs nothing and speaks within seconds. The row says "checks on its own, no AI" or "asks Claude each morning".
- **No hidden schedulers.** The machine's Claude session runs with `--disallowedTools CronCreate,CronDelete,CronList,ScheduleWakeup,Monitor,RemoteTrigger,PushNotification`.
- **The promise check.** A Stop hook for the machine's session only matches a small phrase table ("I'll let you know", "I'll remind", "I'll keep an eye") and, if the turn made no promise() call, blocks with "You told Daniel you would follow up, but nothing will wake you. Call promise() or say plainly that you won't." The system prompt says the same in one rule.
- **Autopilot.** agentd lists `bombadil-*` user units and the fixed care units (fstrim, btrfs scrub, snapper cleanup, smartd) with their next and last times over D-Bus; an os-mcp list_autopilot tool answers the agent from the same truth; "autopilot", "hold everything", "pause <name>" and "stop <name>" skip the model. Any other user timer or crontab shows as "Something else runs on a timer: foo.timer".

**Effort:** M

### 5. What it knows, and what it is holding

**What you see:** When the agent learns something about you, the line says so as it happens ("Remembering: you like the dark theme"), and the closing line gets a small chip, "Learned 1 thing", with Forget. If the fact came from a web page or a file rather than your own words, the chip is amber, says "from a page", and the fact is kept only if you tap Keep; nothing waits on that tap. Undoing a turn also takes back what it learned, and the undo line says so. Click the dot while nothing runs, or ask "what are you holding?", and a card rises with the agent's mind in a few rows: now, your waiting asks, promises and when each speaks, what it is waiting on (the ISO at 43%, Claude's limit until 15:00), and unfinished things ("VPN kill switch not set up yet"), with a footer counting what it remembers about you. A thin ring on the dot shows how much of your plan's window is used: invisible below half, faint grey from half, amber from 80% with the line the dev brief decided, and at 95%, with Codex signed in, the closing line offers "Use Codex until 15:00" before you hit the wall.

**Why:** One memory you can read is a confirmed rule, but Claude Code's own auto memory is on by default and would grow a hidden second memory under ~/.claude that Codex never sees and you never read, and one injected page could write itself into every future session. The passenger should see the moment of learning, and see what the machine is carrying, without asking the AI.

**What changes:**

- **One memory.** agentd sets CLAUDE_CODE_DISABLE_AUTO_MEMORY=1 and passes `autoMemoryEnabled: false, autoDreamEnabled: false` in the Claude process's settings (Codex memories are off by default and the skel config keeps them off), and reports an error if system/init still lists an auto memory path.
- **remember(fact, source) and forget(match)** in os-mcp edit memory.md atomically. narrate says "Remembering: <fact>" for them and for any edit to memory.md or its CLAUDE.md and AGENTS.md links; turn_end carries learned and forgot. A fact whose source is not your words, or that arrives in a turn that read a page and is not in your prompt, goes to ~/.local/state/bombadil/memory-held.md, which nothing loads, until Keep moves it over without a model call.
- **Loose ends.** unfinished(text) and finished(id) keep at most seven open rows tied to their turns in ~/.local/state/bombadil/unfinished.jsonl; they fade after a week, are closed when their turn is undone, and reach the model only in a fresh-page seed or when asked, never on every turn.
- **The holding card.** agentd's holding() aggregates the queue, promise units, jobs, sessions waiting on you, provider limits, connectivity, unfinished rows and memory.md's size, for the shell (a built-in list card), for the words "what are you holding?" and "holding" (which skip the model) and for an os-mcp tool. At rest the dot's click opens it; while a turn runs the dot is still Stop.
- **Fuel.** providers.Claude.parse starts handling the rate_limit_event that `claude -p` already emits before system/init (agentd drops it today); Codex's limits come from its app-server's account/rateLimits/read after a turn, at most once a minute, with no ring if that fails. The ring is an arc of QtQuick Shapes around the dot.

**Effort:** M

### 6. Updates you can watch

**What you see:** You never see a terminal or a pacman screen. A click on the clock shows one quiet row, "214 updates, 4 of them security fixes", and the same count is in the welcome-back line; nothing pops up. Type "update", press Update now, or say once "keep it updated every Sunday night". The line says "Saving a restore point" and counts through the work ("Updating Chromium, 84 of 214"), and because the downloads happened in the background it usually takes a minute or two. If Arch has posted a note that needs a manual step for something you have installed, the line says "Arch says mkinitcpio 42 needs a step first. Doing it." before anything else. Something that only exists in the AUR is read before it is built ("Reading the AUR recipe for spotify-launcher"), and a recipe that looks wrong is not built, with the evidence in one line. At the end the line says it the way you would: "Updated 214 things: Chromium, Hyprland and 4 security fixes. Restart to finish: new kernel." Details groups the rest (apps you use with old and new versions, security fixes, the system, the other 190 folded into a row). Esc halfway gives "The update stopped at 84 of 214" with Finish it or Restart and go back.

**Why:** Keeping a rolling distro current is the one chore every Arch user has, and the first brief lists it as a gap ("Updates on rolling Arch as a background job inside a snapshot, reported as one line about what changed and what needs a restart, never as a terminal."). A passenger should never do upkeep, and upkeep should never happen behind their back: the AUR had a wave of malicious packages in June 2026, and an update is the one turn most likely to break the machine.

**What changes:** A system timer, bombadil-updates-check, runs `checkupdates -d` (a private copy of the package lists, downloads into the cache, never installs), skipped on battery or a metered link, plus `arch-audit -u` for security fixes, AUR and Flatpak checks, and the Arch news feed, into /var/lib/bombadil/updates.json for the clock card and the welcome line. "update" and "updates" join the launcher's exact words. The update itself is a shell turn running `bombadil-update`, so it gets the restore point pair, the live line from its output and the receipt with nothing new: it refuses while an undo waits for its restart, checks space, sends matching Arch news to a model turn first (marked as data), runs `pacman -Syu` (with snap-pac skipped, since the turn already has its pair), reads AUR recipes before building (`bombadil aur-diff` shows the diff since the last reviewed commit, and exactly that commit is built), updates Flatpaks, merges .pacnew files with the agent, and prints plain progress lines. The receipt is built locally: names from each package's desktop file, the restart reason from the running kernel and core libraries, "reopen to use the new version" from /proc maps with deleted files, and kernel-modules-hook so the running kernel keeps its modules until the restart. A schedule said in words is a routine from piece 4. Firmware is never part of it: "update the firmware" is its own red turn, because no restore point can take it back.

**Effort:** M

**Next, after these six:**

- **What broke it.** Local probes with no model (a full disk, including btrfs running out of unallocated space while df still shows room; a service that keeps failing, through an OnFailure drop-in for every unit; SMART errors; battery wear; out-of-memory kills; a turn with no restore point) feed the desk's Machine card, and each one names the change that probably caused it: "Bluetooth stopped working 3 minutes after last night's update, which replaced bluez." The first button is then "Undo just that", with "Fix it instead" beside it, and nothing is fixed until you tap. This is the machine and what you installed on it; Bombadil's own bugs belong to the self-improvement loop.
- **A copy somewhere else.** Restore points live on the same disk as what they protect, so a dead SSD or a stolen laptop takes the undo with it. "Keep a copy on my USB drive" (or a cloud bucket) becomes a routine in Autopilot with "last copy 2 h ago", and "what's in my backup?" draws what it holds on the Map's places. restic was the finder's choice, not checked here.
- **Secrets stay out of sight.** Passwords and tokens that pass through a command or its output are masked on the line, in Details, in turns.jsonl and in the brain, with a small "hid 1 secret" mark. Codex already keeps variables named like KEY, SECRET or TOKEN out of the shell by default (not checked here); Claude's session gets the same rule from agentd.

## A day riding along

This is how a day should feel once the six pieces and the best of the rest are built. Most of it does not exist yet.

**Morning.** The welcome line (its words are the voice thread's) counts "214 updates, 4 security fixes", and the desk's Away card has one row from the night: Tidy Downloads ran at 03:04 and moved six installers to the Trash, with Undo.

**A VPN.** "set up a VPN to my office and start it at login". The desk's Now shows four stops. Hovering the line gives the reason for each; the step that writes /etc/wireguard shows "after reading wireguard.com/quickstart". The closing line comes with the network picture before and after, the tunnel lit orange.

**Slow internet.** "why is the internet slow?" draws the chain at once: the Wi-Fi at 34%, the router fine, Claude answering in 90 ms. The weak link is amber with "You're far from the router; the 5 GHz band drops first."

**The browser.** "renew my library books". The browser slides in with the orange frame, the pointer finds the sign-in, and the line says "Sign in, then say continue." You type the password yourself, say "continue", and it renews three books and closes the panel.

**A promise.** "tell me when the Ubuntu ISO is downloaded". The chip flies into the dot. You restart for the kernel before lunch; at 14:20 the pill says "Ubuntu ISO downloaded and checked. [Show]".

**Memory.** A forum page it read while fixing your printer said "the user prefers the Windows driver". The closing line's chip is amber, "from a page". You leave it; it is never kept.

**What it is holding.** Before a flight, a click on the dot: one promise, one unfinished thing ("printer duplex still off"), Claude at 62% of this window. "hold everything". Routines and care pause until you say otherwise.

**Evening.** "update". Saving a restore point, 214 of 214 in about two minutes, one Arch note handled first, and "Restart to finish: new kernel." Details says Chromium went from 141 to 142.

## Decisions I picked a default for

Each of these forks; I picked a default and say why, with the alternatives for the record.

**Where do the reasons come from?** Default: the agent's own sentence before each step, kept by narrate, plus one system-prompt rule so it is reliably there. It is free, the same on both providers, and recorded before the act. Other options: a small model explaining each step afterwards; Claude's thinking text (often empty in headless runs on current models, not checked).

**Is a change after an outside read paused?** Default: no. It is marked "after reading …", and Esc is in reach. The first brief confirmed no pause before any step, and the mark comes from the event order, so an injected page cannot talk its way out of it. Other options: pause system changes after an outside read.

**Who draws pictures of the machine?** Default: os-mcp captures them from real commands; the model only picks the part and adds a line. Captured pictures are instant, free and cannot misstate the machine. Other options: the model writes diagram JSON from what it read; agent-written QML for everything.

**Which picture shapes?** Default: four fixed shapes (chain, layers, compare, timeline), laid out deterministically, at most about 12 boxes, and a window for anything bigger. They read at a glance and cost a few hundred tokens. Other options: free force layouts; Graphviz; Mermaid.

**How much does it explain?** Default: three stops set only in words, Brief, Normal and Teach, starting at Normal, stored as one line in memory.md and one config key. At Normal a change to one of the six drawable parts brings its before and after; at Brief pictures come only when asked; at Teach system words on the line get a faint chip that opens their picture. The setting never changes by itself. Other options: always on; a settings toggle.

**How does the browser hand over?** Default: a real pointer move or key press on the page ends the machine's browser use for that turn, and "continue" is an ordinary turn. No pause mechanism, and os-mcp refuses browser calls after a takeover. Other options: a Take over button; freezing the whole turn.

**Where does the agent work in the browser?** Default: its own tab, unless the ask is about the page in the "this" chip. Other options: always the current tab.

**How are promises kept?** Default: persistent user unit files with the words inside, the only scheduler the machine's agent has, with the vendors' own schedulers turned off for its session and a Stop hook that catches "I'll let you know" without a promise. Other options: agentd's own scheduler file; the vendors' cron tools.

**When do routines run?** Default: the first run now, as your ask; later runs only while you are away unless you named an exact time, one at a time in the ordinary queue, each with its restore point; two failures in a row stop it. Watching the first run stands in for watching every run. Other options: run exactly on schedule; ask each time.

**Do watchers ask the AI each time?** Default: no, whenever a check or script can do it; the row says which. A watcher that called the model every ten minutes would use up a day's limit on nothing.

**Where does memory live?** Default: memory.md only; Claude's auto memory and dreaming off in the machine's session, Codex memories kept off; facts from pages or files held until you tap Keep. Other options: vendor memories on beside it; everything kept, with Forget.

**When do updates install?** Default: checked and downloaded quietly; installed only on your word or a schedule you set in words, as a turn. Other options: install at night on their own inside a restore point (the finders' other proposal, cut below).

**What about Arch news that needs a manual step?** Default: the update turn does the step first and says so on the line; with no provider available the update waits and says why.

**What about the AUR?** Default: the agent reads every recipe, or its diff since the last reviewed commit, before building, builds exactly that commit, and stops only on evidence; "install it anyway" overrides. Arch asks every user to do this review, and the agent is the one who can actually read it.

**How does Bombadil itself update?** Default: as pacman packages from its own signed repository, riding the ordinary update in the same restore point, so undo covers a bad Bombadil release too. Today nothing updates Bombadil on an installed machine, and the Hyprland config from skel is copied once. Other options: a separate self-updater; reinstalling from a new ISO.

**Firmware?** Default: only when asked, as its own red turn that says it cannot be undone, never inside the routine update.

**One model per ask?** Default: one model per session, with effort picked per ask by a local rule table (short asks low, building or fixing high), and "Think harder" on a closing line to redo an answer with full effort. Changing the model mid-conversation breaks the prompt cache; changing effort does not. This designs the first brief's gap "Choosing the model for each turn", redirected from --model to --effort.

**When is the fuel ring visible?** Default: from 50%, amber at 80% with the decided line, and the switch offer at 95%. Always-on numbers would turn the pill into a dashboard.

**What happens when a drive is plugged in?** Default: it mounts read-write at once with Open and Eject in one pill line, because plugging it in is the ask, and the agent and the brain can only help with a drive they can see. Other options: mount on click, which is a small permission prompt by another name.

**What does closing the lid do?** Default: sleep, except on external power while a turn, a job, a print or a coding session is working; then the machine locks, turns the screen off and keeps working, with a crossed-out moon by the clock saying why and a Sleep anyway button. On battery a closed lid in a bag always sleeps. Other options: always sleep; never sleep on power.

## Everything else, by moment

### While it works

- **The dot at a glance.** The dot breathes while it thinks, becomes an amber or red ring while it changes the system, a tiny pointer while it acts on the screen, and a slowly filling circle while it waits, with the line naming what it waits for: "Waiting for pacman to download · 40 s", "Claude is busy, trying again in 8 s".
- **Orange hands on windows.** A window the machine opens, moves or changes glows orange at its edge for a second; your own moves never glow. "what did you just touch?" flashes them again.
- **Watch again.** A turn you missed plays back in about ten seconds from its event log: the route, each step with its reason, what it read, what it changed, and small page frames for browser steps, with "Undo just this" and "Ask about this" on any step. Opened from Details and from the Brain's Time strip.
- **Asks that wait for the internet.** Offline, an ask becomes a grey "when online" chip and runs when the connection returns if it is under an hour old; older ones wait for Tab. Retries are counted on the line, and a provider that stays down offers the other one with one button.

### Understanding

- **Why? on anything.** A small "?" on hover over a card row, a box in a picture, a chip or the line, and the pill words "why?" and "what's this?", answered first from records in a fixed order (the step's reason, who started a process, the brain's history, a concept's picture) and only then by the agent.
- **What's DNS?** A shipped, reviewed book of about forty system ideas, each a sentence and a picture filled in with this machine's values ("Yours asks 1.1.1.1. The machine set that in turn 41, 'install the VPN'"), with Show me mine. Instant and offline; it is not the cut agent-kept wiki, because it is shipped and its values are read fresh.
- **When something breaks, the chain of why.** At most four boxes, each pinned to a log line, an exit code or a check ("Turn 57 changed resolv.conf, names stopped working, put back 3 files, working again"), anything guessed drawn dashed and saying "probably". The machine's own rescues show theirs at Normal; other failures build theirs on Why?.
- **A text twin for every picture.** Generated from the same data, for copying, captions and screen readers, so it can never disagree with the picture. Reading answers aloud belongs with the voice thread's speech service.
- **One motion and sound language.** Everything comes out of the pill and goes back into it; what the machine changed glows orange and fades; undo plays a change backwards; nothing bounces. Sounds stay off apart from the timer's chime, and "sounds on" adds a done tone for turns over ten seconds and a low tone when something breaks. This designs the first brief's listed gap.

### What it knows

- **A fresh page you can see.** When the agent starts a fresh session, it waits for a quiet moment between asks, carries a fixed visible bundle (memory, the last five asks, open promises, unfinished rows), and marks the welcome line with a small page glyph that lists what came along. After a crash, a turn that was cut off comes back as a dim chip with Continue and Undo it; nothing runs by itself.
- **A small local model for when the cloud is gone.** Opt-in on capable hardware, reached only through the offline line's button, limited to small asks, with big ones waiting as "when online" chips.

### The machine

- **Laptop basics** (the first brief's listed gap). Keys, a level in the pill, Print Screen, tap to click, HiDPI, and the clock's status card. Closing the lid sleeps, except on power while a turn, a job, a print or a coding session is working: then the screen locks and goes dark, and a crossed-out moon by the clock says "Staying awake: builder is working" with Sleep anyway.
- **Plugging something in explains itself.** "SanDisk USB drive, 64 GB, 12 GB used. [Open] [Eject]"; a phone that is only charging says how to get its photos; headphones say where the sound went. Drives mount read-write on plug-in, because plugging it in is the ask.
- **Several screens.** Each screen keeps its own pill and stage, and anything you ask for opens on the screen you asked from. Layouts are remembered per monitor.
- **Mic, camera and screen marks that say who.** A mark on the dot coloured by who is using it (white an app, orange the machine, blue a session), with a switch that cuts the device for everyone; "camera off" works offline.
- **Why is it slow?** Answered locally in a fraction of a second, with what the machine waits for ("Mostly waiting for memory") and bars by who, each with Stop, Put to sleep or Close. This extends the desk's Machine card, which already answers "How's the machine?".
- **This computer.** Before install the live USB lists what works and what needs a driver, in plain words, with everything that works folded into one green line; NVIDIA's open driver, SOF, Broadcom and Bluetooth firmware are on the ISO.
- **Next to Windows.** The install card draws the disk as a bar with Windows, free space and the slice Bombadil would take; it shrinks only a clean NTFS volume and explains BitLocker's one-time recovery key before you press anything.
- **Print and scan as jobs.** "print this" prints to the last printer with a page-counting chip; "scan this page" saves a PDF that the brain knows came from the scanner. Driverless IPP means nothing to install for most printers.

### Apps, space and change

- **Ordinary Linux apps** (the first brief's listed gap). "install Spotify" says where it comes from ("from Flathub, 180 MB"); Arch's repositories first, then Flathub, then the AUR through the recipe check. Installed apps open by name through the launcher and follow the theme.
- **Where the disk went, and how far undo reaches.** "what's eating my disk?" always includes restore points ("18 GB, undo reaches back to Tuesday"), downloaded updates and app runtimes, each with a button. Logs and the package cache get their own subvolumes so they never roll back, and a nearly full disk is cleared in a fixed order with one line saying how far undo still reaches.
- **What's different from stock.** "what have you changed on this machine?" opens the Brain's System area on one list in plain groups, each row with who changed it, why in your words from the time, and Put back.
- **The machine as a recipe.** A readable page of everything on top of stock Bombadil, each line with its reason, kept current after every changing turn; "set this up like my old laptop" redoes it on new hardware as one turn, skipping what does not fit with a reason.

### Trust (mine, not checked by a second pass)

- **Lock and encryption** (the first brief's listed gap: "Lock screen and encryption: lock on lid close and when idle, a user password, and full-disk encryption offered at install. The agent has sudo and your accounts are signed in."). Default: full-disk encryption on by default in the install card, unlocked by the user password (a TPM unlock later), and hyprlock on lid close and idle, with the pill hidden and notifications showing no content while locked. A machine with an agent that has sudo and signed-in accounts is worth more to a thief than an ordinary laptop.
- **A keyring from the first login.** Without a secret service, Chromium on Linux falls back to a fixed key for saved passwords and cookies (not checked here). gnome-keyring unlocked by the login through PAM, so Chromium, gh and the CLIs store secrets properly.
- **Who talks to the internet.** The network picture gains a "who" view: connections and bytes by the machine's turns, coding sessions and apps, from per-cgroup accounting, coloured like everything else. "what did you send out today?" is a real question on a machine that reads and writes on your behalf.
- **A clipboard that forgets passwords.** Clipboard history ("paste what I copied before") skips anything marked sensitive by the app that copied it and clears after a while.
- **Someone else at the keyboard.** Anyone at an unlocked Bombadil can ask the agent anything with your sudo and your accounts. Not designed here; the lock is the answer for now.

### Beyond this machine (mine, not checked)

- **The phone.** Promises and "something broke" lines reach the phone when you are away, and "what's the machine doing?" works from it, extending the dev brief's Remote Control path from coding sessions to the machine's own conversation. The brain brief lists "The brain from the phone" as not designed; this is the same gap.
- **A second Bombadil.** The recipe covers rebuilding once; keeping two machines' memory.md, routines and chosen folders in step (Syncthing was the finder's lead) is not designed.
- **Mail, calendar and people.** The brain brief's listed gap; Gmail and Outlook now require OAuth for mail clients, so this starts with a guest app such as Thunderbird rather than something the agent builds.

### OS basics (mine, not checked)

- **Languages, keyboards and time.** The first boot asks nothing, but "add a Greek keyboard", "type Japanese" (fcitx5 on Wayland) and "answer me in Portuguese" are words; the clock follows the time zone when you travel, and dual-booting with Windows keeps the hardware clock in UTC.
- **Notifications from other apps.** Chromium, Thunderbird and the rest notify through the shell as the decided one pill line, filed in history, never a toast.
- **One theme for guests too** (the first brief's listed gap "One live theme for the whole machine"). Dark and light reach Chromium and GTK and Qt apps through the desktop portal's appearance setting, so "make everything warmer" restyles guests too.

## Owned by other threads

- **The Bombadil Desk.** Now draws the route that piece 1 turns on; Watching shows piece 4's jobs; Away shows piece 4's routine runs and the night's upkeep; the Machine card is where "What broke it" appears. This brief feeds those cards and never draws its own.
- **Bombadil's voice, and welcome and empty states.** The words of the welcome-back line and of empty states are that thread's; this brief only adds what the line counts (updates, promises that came true).
- **The self-improvement loop.** Noticing repeated asks and offering a widget, app or routine; Bombadil finding, reporting and fixing its own bugs; growing the kit. A routine it proposes lands in Autopilot like any other, and Bombadil's own updates ride piece 6's update.

## Cut, and why

- **Updates installed at night on their own.** One of the finders proposed it; it would have the machine start work nobody asked for, and a background install beside a turn breaks "one turn, one restore point". Downloading at night is kept; installing waits for your word or your schedule.
- **A route strip under the line.** The desk's Now owns the route; this brief turns the plan tools on and sends the plan event.
- **A while-you-were-away card of its own.** The desk's Away is that card.
- **A second model narrating the turn.** Slower on every turn, and two narrators disagree.
- **Pictures of the machine written by the model.** A passenger cannot check them.
- **Free force-layout diagrams.** Unreadable past a dozen boxes, and different every time.
- **An "Explain more?" offer learned from repeated Why? presses.** Learning from repeated asks belongs to the self-improvement loop.
- **A local model installed by default.** Gigabytes and memory on every install for a feature most days never use.
- **Mounting drives only on a click.** A small permission prompt by another name.

## Gaps worth thinking about

### In the code today (main at 089f181)

- narrate.Narrator clears the agent's words at every tool start, so the sentence before a step is lost.
- providers.py does not turn the plan tools on, and Codex.parse turns a whole todo list into one fake TodoWrite item.
- show_card exists only as a name in narrate.py; no branch defines the tool or a card host.
- os-mcp has no browser tools, and the panel's Chromium still starts without its own --user-data-dir.
- turns.jsonl rows have no list of what a turn read or changed.
- providers.Claude.parse drops rate_limit_event, the only live source of how much of a plan window is used.
- Nothing schedules anything: no promise, watcher, routine or timer code exists on main or the branches checked.
- The installer still mounts the ESP at /boot, so a kernel update is not inside the restore point until the confirmed move to /efi lands; snap-pac is not in the image.

### Not designed yet

- A shape for the rare question the agent really must ask (the first brief's gap), which printers, networks and "which of these?" all need.
- Someone else at the keyboard: a guest, a child, a colleague.
- The phone as a full remote for the machine's own conversation.
- Keeping two Bombadils in step, and mail, calendar and people.
- The speed budget (the first brief's gap): pictures, reasons and the holding card each add to what happens after Enter, and should be timed on real hardware.

## What would make it a chatbot with a dashboard

- Explanations as paragraphs.
- A second voice narrating what the first one did.
- Pictures of the machine that the model made up.
- "Are you sure?" before changes, instead of the reason beside them.
- A promise in words that nothing will keep.
- A memory you cannot read beside the one you can.
- A maintenance app, or a terminal for updates.
- Gauges that are always on.
- Background work you cannot list or stop.

## Extends or changes the earlier briefs

- **"How much of the screen does the agent's text take?" (one line while working):** extended. Hovering the line shows the step's reason and, on marked steps, "after reading …"; nothing is added without hover.
- **What skips the model:** extended with "why" during a turn, "what are you holding?", "holding", "autopilot", "hold everything", "pause <name>", "stop <name>", "update", "updates", "explain less|more|normally", "camera off|on", "mic off|on" and "eject".
- **Answer cards (first brief piece 5):** extended with one typed kind, "diagram", built first together with the card host; the other kinds and the agent-written QML cards stay as decided.
- **The closing line is the receipt:** extended with a before-and-after picture for the six drawable parts at Normal, and with the Learned and Unfinished chips.
- **The system prompt:** gains rules for plans in plain words, a reason before each system change, pictures for answers with parts, order or change, promise() instead of words, and handing over the browser at sign-ins.
- **Watchers ("a systemd user timer that speaks through the pill"):** extended to persistent unit files with the words inside, promises, routines that act, compiled checks, and one Autopilot list that "what are you watching?" opens.
- **"The agent never starts work on its own":** extended narrowly. A routine you set up in words is your own ask with a time attached; nothing runs that you did not ask for, and installs never start on their own.
- **One memory you can read:** extended with the moment of writing, Forget, the held state for facts from pages, and switching off the vendors' own memories.
- **The dot:** at rest a click opens the holding card; it gains a fuel ring and shapes for thinking, changing, acting and waiting. While a turn runs it is still Stop.
- **One conversation, fresh sessions:** the seed also carries promises and unfinished rows, a size reset waits for a quiet moment, and a page mark shows what was kept.
- **Provider trouble is one sentence and one button:** the button is offered at 95% of the window, before the wall.
- **Updates (first brief gap):** designed as a turn on your word or schedule, with a local receipt; the second brief's CLI updates at quiet moments are unchanged.
- **Chromium over CDP (foundation choice 7):** the browser tools live in os-mcp on Playwright over CDP, with takeover and "continue".
- **Rewind in the Brain's Time strip:** gains Watch again and small page frames for browser steps, under the same history switch.
- **The desk:** Now reads the plan event from piece 1; the Machine card gains the probes and "Undo just that" from What broke it.
- **The brain's "came from" links:** fed by the turn's read list.

## Checked, and what still needs checking

**Checked today (29 Sep 2026):**

- Claude Code gives TaskCreate, TaskGet, TaskUpdate, TaskList and TodoWrite by default only on Claude 3.x, Opus 4 to 4.7, Sonnet 4 to 4.6 and Haiku 4.5; on other models they are left out unless CLAUDE_CODE_ENABLE_TODO_TOOLS=1 is set or a Task tool is named in --allowedTools; cloud and background sessions always get them, which is probably why a fixture recorded in the cloud showed TaskCreate (code.claude.com/docs/en/tools-reference, "Task tool availability"). The desk's build thread has been told.
- Codex registers update_plan only when `[tools.update_plan] enabled = true` (codex-rs/core/src/config/mod.rs, resolve_update_plan_enabled defaults to false).
- main is at 089f181 (the drawer fix, PR #6), and show_card appears only in narrate.py on every branch.

**Checked by the finder that proposed each piece (27 Sep 2026, sources in the gap records):** Claude streams the text before a tool call as text_delta and narrate clears it at tool start; Codex exec emits each agent_message as item.completed mid-turn and reasoning items only when non-empty; Qt Quick Shapes prefers one Shape with many paths, and Canvas in Qt 6 is image-only; `ip -j`, `lsblk -J`, `findmnt -J`, `pw-dump` and `hyprctl -j` give JSON, systemctl has no JSON and critical-chain is text; Playwright 1.59 added Screencast.show_actions and show_overlay; Chromium 136 and later ignore the debugging port on the default profile; transient user units live in $XDG_RUNTIME_DIR and vanish at reboot, Persistent= works only with OnCalendar, and X- sections are ignored; Claude's CronCreate tasks are session-bound and background tasks end about 5 s after a `-p` result; Stop hooks on both vendors can block with a reason; Claude's auto memory is on by default and turned off with CLAUDE_CODE_DISABLE_AUTO_MEMORY=1; `claude -p` emits rate_limit_event before system/init; checkupdates -d, arch-audit -u, pacdiff, checkrebuild and kernel-modules-hook behave as described; Arch reported malicious AUR packages on 12 June 2026.

**Not independently re-checked:** the usage limit stopped the second, adversarial check of all nine slices, so a build thread re-checks the facts under its piece before building. The trust, other-machine and basics items were written by me without a finder and are marked as not checked.

**Unverified, to test in the VM before pieces 1 and 2:** that the current Claude model writes a usable sentence before most tool calls in `-p` mode, and Codex before commands; that the plan tools appear in `claude -p` stream-json with the variable set, on the model Bombadil uses; that each sysmap capture finishes in under 500 ms on real hardware; and that streamed diagram nodes arrive early enough to matter (a 12-node diagram is roughly 300 to 500 output tokens, an estimate). Before piece 3: that show_actions draws in the live window and not only in recordings, and that CDP clicks do not move the compositor's pointer.

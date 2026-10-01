# How Bombadil should feel

> **Status:** Partly shipped
> **Code:** `src/bombadil/agentd.py`, `src/bombadil/narrate.py`, `src/bombadil/launcher.py`, `src/bombadil/hypr.py`, `src/bombadil/procs.py`, `src/bombadil/appkit/runtime.py`, `src/bombadil/appkit/tools.py`, `src/bombadil/cards.py`, `src/bombadil/cardtools.py`, `src/bombadil/signin.py`, `shell/shell.qml`, `shell/StatusLine.qml`, `shell/PillState.qml`, `shell/QueueChips.qml`, `shell/SetupChips.qml`, `iso/airootfs/etc/skel/.config/hypr/hyprland.lua`
> **Design:** [How Bombadil feels (page)](pages/how-bombadil-feels.html), [UX model](../ux/README.md), [Design system](../design-system/README.md)
> **Decided by:** the project owner, 2026-09-27 (the defaults under "Decisions I picked a default for")
> **Verified:** 2026-10-01 against `main` at `26843d3`: the statuses under "Build these first" and the files they name. The "Today" sentences, the list "In the code today" and the vendor facts are the brief's original text, dated 2026-09-27, and were not re-checked (see the reading note).

You turn it on and see your wallpaper and one pill at the bottom of the screen. That is the whole interface until you ask for something. When you do ask, the machine answers by doing it: the browser slides in, a slider appears where you need it, or a native window builds itself in the middle of the screen, and one plain line tells you what it is doing while it does it. Nothing asks for permission, because everything is visible and "undo" puts it back. You learn three things: Super to talk, Esc to stop and "undo" to go back. You find everything else by asking.

> **Reading note.** Sentences that say what the code does or what exists ("Today ...", "Most of it does not exist yet", the list under "In the code today") describe the code on 2026-09-27, the day the brief was written, and several of those gaps have been fixed since. [Known issues](../known-issues.md) and the [roadmap](../roadmap.md) say what holds on `main`, and the list under "Build these first" says how far each of the six pieces got.

## The rules

1. **One pill, and three things to learn: Super to talk, Esc to stop, "undo" to go back.** Everything else appears when you call it and leaves when it is done, so nobody needs a manual.
2. **The answer is the thing itself.** A window, a card or a panel appears, and written replies stay at one or two lines, because a paragraph about the work turns an OS back into a chat window.
3. **Something true is on screen within 200 ms of Enter, and open, stop and undo never wait for the model.** If nothing happens after Enter, people press it again or decide the machine has frozen.
4. **No prompts, and nothing hidden.** With no guard rails, safety comes from two things: every change shows in plain words as it happens, and undo says exactly what it covered.
5. **"This" means what is in front of you.** People talk about what they can see, and nobody should have to describe their own screen to their own computer.
6. **What the agent makes is a real native app.** You open it by name, change it by talking and roll it back on its own, and it never runs as a web page on a port, because that is what separates an OS from a chatbot that writes code.
7. **Recovery never goes through the part that broke.** The agent can edit the shell, the compositor config and agentd itself, so stop, undo and rescue must work without the model, the network or the GUI.
8. **One machine, one conversation, one memory you can read.** The OS lives in os-mcp and in plain files, so Claude and Codex run it the same way and switching between them loses nothing.

## Build these first

Build these six pieces in this order. The first two change how every ask feels. The middle two are the core promise: native apps, and an agent that sees your screen. The last two are what people use most often and see first.

Where each piece stands, checked against `main` on 2026-10-01:

- **1. See what it is doing: shipped, except the kept-alive Claude process.** The "On it" line with its seconds counter, the plain-word steps, the amber and red edges with the exact command, the grey "next" chips and Details in a `bombadil watch` terminal are built (`shell/PillState.qml`, `shell/StatusLine.qml`, `shell/QueueChips.qml`, `src/bombadil/narrate.py`, `src/bombadil/watch.py`). `AgentD.turn` in `src/bombadil/agentd.py` streams each CLI line as it arrives, reads stderr during the turn and sends `turn_start` before the restore point. Not built: the kept-alive Claude process with `--input-format stream-json`. `Claude.command` in `src/bombadil/providers.py` still starts a CLI for every turn.
- **2. The pill is the launcher, and Stop means stop: shipped.** `src/bombadil/launcher.py` matches exact words, the alias table and the fixed commands, and `Launcher` acts through `src/bombadil/hypr.py` without a turn. `AgentD.local` and `AgentD.stop` in `src/bombadil/agentd.py` run them and log them, and a line that starts with `!` runs a shell command. Each turn runs in its own systemd scope and Stop ends all of it (`src/bombadil/procs.py`). The Super tap, Super+Escape and Alt+Space are Hyprland binds (`iso/airootfs/etc/skel/.config/hypr/hyprland.lua`), and Esc and the dot's Stop are in `shell/shell.qml`. Undo is local, but it is still the version that waits for a restart: it says that system files come back on the next restart (`Launcher._undo` in `src/bombadil/launcher.py`, `src/bombadil/snapshots.py`).
- **3. Apps change in front of you: partly shipped.** The runtime owns one window per app, swaps the new interface in place, keeps the last good one with a red banner when a load fails, reloads the Python backend when `app.py` changes and writes a status file; `create_app` returns the check result with a screenshot (`src/bombadil/appkit/runtime.py`, `src/bombadil/appkit/tools.py`). Not built: a git repository per app with a commit at the end of each turn, undo for apps, and the two system-prompt rules. `create_app` still takes the complete files, and `src/bombadil/snapshots.py` covers the root only.
- **4. "This" means what is in front of you: designed.** `shell/shell.qml` has no "this" chip (it draws only the setup chips, the queued "next" chips and the running-app chips) and nothing writes a `[Screen]` block. Two supporting parts exist: the browser panel runs with its own `--user-data-dir` and debugging port (`src/bombadil/browser.py`), and the narrator reads a `[Screen]` block when a prompt carries one (`prompt_reads` in `src/bombadil/narrate.py`).
- **5. Answers you can touch: partly shipped.** `show_card` validates `diagram` cards (chain, layers, compare and timeline), follows them while the agent writes them and draws them above the line (`src/bombadil/cards.py`, `src/bombadil/cardtools.py`, `shell/CardHost.qml`). The machine draws the same pictures for exact words and as receipts after a turn (`src/bombadil/sysmap.py`), and timers and background jobs show as rows on the desk (`src/bombadil/jobs.py`, `shell/DeskState.qml`). Not built: the list, checklist, timer, control and markdown reader kinds (the `show_card` schema accepts only `diagram`), sliders that run `wpctl`, `brightnessctl` or `nmcli`, the click on the clock, and the reader that slides in from the right.
- **6. First boot happens in the pill: partly shipped.** agentd asks which AI, runs the CLI's login in a pseudo-terminal with its page in the browser panel, and says "No internet" with a Wi-Fi chip when it cannot reach the sign-in page (`src/bombadil/agentd.py`, `src/bombadil/signin.py`, `shell/SetupChips.qml`, `shell/PillState.qml`). `bombadil-browser.desktop` is the default web handler, the Chromium policy turns off the default-browser prompt and the promotions, `--no-first-run` keeps the first-run page away, and the image installs both CLIs (`iso/airootfs/etc/xdg/mimeapps.list`, `iso/airootfs/etc/chromium/policies/managed/bombadil.json`, `src/bombadil/browser.py`, `scripts/build-iso.sh`). Not built: Wi-Fi networks as chips with the password typed in the pill (the Wi-Fi chip opens `nmtui` in the details drawer), the three example chips and the first-day hint line, and a Codex device-code login. agentd names its states `choose`, `checking`, `signed_out`, `offline`, `signing_in` and `ready`, not the four named in the text of piece 6 below.

### 1. You can see what it is doing, as it happens

**What you see:** Within a fifth of a second of Enter, the line above the pill says "On it" with a seconds counter, so you never wonder whether it heard you. The line then rewrites itself in plain words as the work goes on: "Opening the browser", "Installing ffmpeg", "Building Passwords, 84 lines". A step that touches the system (sudo, pacman, /etc or a service) gets an amber edge with the exact command underneath, but it never pauses, and anything you type in the meantime waits as a grey "next" chip you can remove. When the turn ends, the line becomes one sentence ("Made Passwords."), and clicking it shows every command and its output in a terminal panel.

**Why first:** Today agentd collects all of the CLI's output and parses it only after the process exits (agentd.py, around line 128). So during a long turn you see a pulsing dot, then everything arrives at once. Almost every other idea needs these live events, and with no permission prompts, watching the work is the safety model.

**What changes:** agentd broadcasts each line as it arrives. It also reads stderr while the turn runs, because today a CLI that writes a lot to stderr can fill the pipe and hang the turn. It adds --include-partial-messages for Claude, sends turn_start before calling snapper, and maps tool events to plain verbs with a small rule table. shell.qml replaces the transcript with a single label, and the command view starts as a `bombadil watch` TUI in foot. The second step keeps one Claude process alive with --input-format stream-json, so the first words arrive in about a second. Before relying on it, check that it works with --resume, the MCP config and bypassPermissions. Codex keeps starting a new process for each turn.

**Effort:** S for the live line, M once the warm Claude process is added.

### 2. The pill is the launcher, and Stop means stop

**What you see:** Tap Super from anywhere and the pill is ready for typing, with no click needed. Type "passwords", "browser" or "chrome" and it opens in under a tenth of a second with no model call. If that app is already open, its window comes forward instead of a second copy starting. "Undo", "history", "wifi" and "lock" work the same way, even offline. Until live undo exists, "undo" says plainly that system files come back on the next restart. Esc in the pill, Super+Escape from anywhere, or a click on the orange dot (which says Stop on hover) ends the turn and everything it started, sudo installs included, and the line says "Stopped while installing docker."

**Why first:** When open, stop and undo take several seconds, the machine feels slower than any other OS, and those are exactly the commands you reach for when you are offline or something is wrong. A local undo also fixes a real bug. Today "undo that" sent through the agent first takes a snapshot of itself, then rolls back to that same snapshot, so it does nothing.

**What changes:** agentd accepts messages that are not turns (open, panel, undo, stop, history). It handles them by calling hypr, apps and snapshots directly, and it logs them to turns.jsonl so the next turn knows what happened. shell.qml acts only on an exact name, an accepted match or the fixed alias table, and open_app focuses a window that is already running. Each turn's CLI runs in its own systemd scope, so Stop can kill the whole group; check on Arch that children started with sudo stay inside the scope. Super and Super+Escape become Hyprland binds that reach the pill and agentd through the socket. Today Super+Escape restarts Quickshell, and the Super tap bind must ignore Super+click and Super+drag.

**Effort:** M (three small pieces)

### 3. Apps change in front of you, and undo in a second

**What you see:** Say "I need a password manager" and within seconds a window titled Passwords appears in the middle of the screen. It starts with a few rows tagged "sample", then fills in with the real thing while you watch. Say "make the rows denser" and it changes where it stands: it does not blink, jump back to the center or lose your entries. If the new code will not load, the last working version stays up with a thin red line ("fixing line 42") while the agent fixes it in the same turn, and "undo that" puts the app back in about a second with no restart.

**Why first:** The app kit was still setting the app runtime contract when this was written, and changing that contract after dozens of apps exist will be expensive. Today an edit blanks the window and moves it back to the center, because the runtime deletes the window and makes a new one. Undo after an app change does nothing, because ~/Apps is in /home, which the snapshots do not cover.

**What changes:** The runtime owns one host window per app and loads the app's root Item into it. It keeps the last good Item when a load fails, restarts the Python backend with persisted state when app.py changes (today the backend is built once and never reloaded), and writes a status file. create_app returns that status along with a screenshot of the window. Each app folder becomes a git repo with a commit at the end of each turn, and data/ is ignored so undo never touches saved data. The system prompt gets two rules: build the skeleton first, and change existing apps with the Edit tool instead of resending the whole file.

**Effort:** M

### 4. "This" means what is in front of you

**What you see:** With an article open, "summarize this" summarizes that page. You can select a paragraph anywhere and say "translate this", or with Passwords in front, say "make this darker". A small chip at the left of the pill shows what "this" means right now ("Arch Wiki: Btrfs", "Selection: 6 lines", "Passwords"). Its x sends your words without that context, so the model never gets something you did not see.

**Why first:** It takes about a day to build, and it is the moment people notice that the agent is part of the machine rather than a chat box next to it. The context goes in as plain prompt text, so Claude and Codex get exactly the same thing.

**What changes:** shell.qml records the focused window when the pill takes focus and draws the chip. agentd puts a short [Screen] block in front of the prompt: the window, the tab URL from CDP, the primary selection from wl-paste and the app's folder. The block is marked as untrusted data, because page titles and selections can carry instructions to an agent that has sudo. Chromium needs its own --user-data-dir. Chromium 136 and later ignore the debugging port on the default profile, so CDP on port 9222 probably does not answer today. Getting the Nautilus selection needs a small nautilus-python extension, because Nautilus's window title only names the folder.

**Effort:** S

### 5. Answers you can touch

**What you see:** "What's eating my disk?" comes back as a small card above the pill with a clickable bar for each folder. "Timer 10 minutes" is a countdown that stays on screen and chimes at zero. "The screen's too bright" dims the screen and leaves a slider that changes the real brightness as you drag, with no round trip to the AI. A click on the clock opens the same Wi-Fi, sound and brightness controls without asking anything. Long answers never grow the reply past 4 lines. They slide in from the right as a reader you close with Esc, and every card goes away once its job is done, a newer one replaces it, or you press Esc.

**Why first:** These cards are the project's "native visual interactives" at the smallest and most frequent scale. They are also what keeps the reply box from turning into a chat transcript.

**What changes:** A new os-mcp tool, show_card, sends JSON for a few typed kinds of card (list, checklist, timer, control, markdown reader), and shell.qml draws them. Because they are data rather than code, they appear at once and cannot freeze the pill. Controls run their get and set commands (wpctl, brightnessctl, nmcli) through Quickshell's Process. Cards written by the agent in QML come next. Each one runs in its own pre-started bombadil-app process, never inside Quickshell.

**Effort:** M

### 6. First boot happens in the pill

**What you see:** After boot the screen is dark except for the pill. With no network, it lists Wi-Fi networks as chips and takes the password in the pill. Next come two large chips, Claude and Codex. Clicking Claude slides the browser in on the sign-in page and back out when you finish, and the pill says "Signed in. Ask me for anything." The empty pill then offers three example chips ("Show me the web", "Make me a password manager", "What's using my memory?"), and for the first day one faint line reads "Super to talk. Esc to stop. Say undo to go back." At no point do you see a terminal, a terms page or a promo tab.

**Why first:** The first minute decides whether this reads as an OS or as a Linux project. Today that minute shows a foot terminal wizard and Chromium's terms page. Two parts of the fix take about an hour each and can ship today: the Chromium policy file, and preinstalling both CLIs in the image.

**What changes:** agentd reports a setup state (needs_network, needs_provider, needs_login or ok) and runs the CLI's login in a pty. A bombadil-browser .desktop file becomes the default https handler, so any login URL slides in the browser panel. Codex has a device-code login that fits in the pill; Claude's login needs wide pty columns and the ANSI codes stripped. Chromium managed policies turn off the first-run page, the promos and the default-browser prompt but leave Google sign-in working, and the foot wizard stays only as a fallback.

**Effort:** M

## A day with Bombadil

This is how a day should feel once the six pieces above and the best of the rest are built. Most of it does not exist yet.

**First boot.** You boot the USB stick on a laptop. The screen is dark except for the pill, which says "No internet" and shows your home network as a chip. You pick it, type the password into the pill, and pick Claude from two large chips. The browser slides in on the sign-in page and slides away when you finish, and the pill says "Signed in. Ask me for anything." A chip offers "Install on this computer", and the pill notes that undo starts once the system is installed. You pick the disk from a plain list, press the red button that names it, and the laptop restarts into the installed system, still signed in.

**First minute.** The browser, the terminal and Files each slide in and back out once and name themselves, which takes ten seconds. Three example chips sit above the empty pill, with one faint line under it: "Super to talk. Esc to stop. Say undo to go back." You press "What's using my memory?" and a card rises above the pill with a bar for each app. You click the biggest one, ask "why is that so big?", and get a two-line answer.

**An ordinary ask.** In the middle of the morning you are reading a long Arch Wiki page. You tap Super, and the chip at the left of the pill says "Arch Wiki: Btrfs". You type "summarize this". "On it" appears at once, then "Reading the page", then four lines that fade on their own. "The screen's too bright" dims it and leaves a slider; you nudge it and it fades. Typing "terminal" drops the terminal in instantly, and typing "stop" in the middle of a long install ends it, sudo and all.

**Something that doesn't exist.** You type "I need a password manager." Within seconds a window titled Passwords rises into the middle of the screen with three rows marked "sample". Over the next half minute it fills in with search and a lock screen while the line counts what is being written, until the line says "Passwords is ready." You say "make the rows denser" and it changes where it stands. You don't like the change, so you say "undo that", and a second later it is back. It is an app now: next time you type "pass" and press Tab.

**A week later.** You come back after a week away. For a few seconds the line says "Welcome back. Since last time you built Passwords and Tracker. 214 updates are waiting." There is no chat to pick, because it simply remembers, and "what do you know about me?" opens a short list you can edit. You type "history" and the week slides up as small screenshots with your own words beside each one, and you find the Tuesday you asked it to block ads.

**When something goes wrong.** You ask it to set up a VPN and the network drops. You don't have to do anything: the network check after the turn failed twice, so agentd put back the three files it had changed in /etc. The pill says "That change cut the network, so I put it back. Redo it?" Later Claude hits its limit and the pill says "Claude is at its limit until 15:00", with a button to use Codex until then. The one time a driver update leaves the desktop unable to start, the screen switches by itself to a plain page in large type with four choices. You press 1, and after a restart the machine is back to how it was before the update.

## Decisions I picked a default for

The project owner agreed with all of these defaults on 2026-09-27, so they are confirmed decisions, not proposals.

**How much of the screen does the agent's text take?** Default: one line that rewrites itself while the agent works, and at most 4 lines when it is done, with two sentences as the goal. The answer fades 12 seconds after the turn, but never while you hover over it or while a card is up. If the turn changed files, its closing line ("Installed Docker.", with Undo and Details) stays until your next prompt. Anything longer slides in from the right as a reader. A text box that grows above the pill is exactly how this becomes a chat window, and the Undo button must not fade while you are looking away. Other options: today's box that grows to 360px; everything stays until the next prompt; a separate receipt card after every turn.

**Do the apps it makes stay?** Default: yes. Every app lives in ~/Apps, survives a reboot and opens by name. The agent extends an existing app with the same purpose instead of making "passwords-2", and "what apps do I have?" and "delete Passwords" make cleanup one step. Cards are the temporary layer: they stay in a cache for 7 days, and a pin turns one into an app. Asking "keep it?" after every build would be a permission prompt in disguise. Other options: temporary until you say "keep it"; ask every time.

**What does "undo" cover, and does it need a restart?** Default: apps undo live through git. Home files undo live once /home gets its own snapper config, and only the files the turn changed are restored; files you edited since then are skipped. System files undo live only when every change was under /etc, and the affected services are restarted. Changes to packages, /usr, /var/lib, the kernel or /boot go back on a restart, and the line says so. Undo never rolls back all of /home, and it always says what it covered. If a config tweak needs a restart to undo, people learn never to use undo. A short list of paths that are safe to revert live is the safer design, because a list of exceptions would miss daemon state under /var/lib. Other options: root only, on restart (today); packages live too; rolling back all of /home.

**Can answers come back as small live widgets instead of text?** Default: yes, when the answer changes over time, is a list you act on, is a comparison or involves a clock. An answer that is a single number stays a sentence. Start with a few typed cards that the shell draws from JSON, then add agent-written QML cards that run in their own process, never inside Quickshell. Typed cards appear at once, look consistent, cannot freeze the pill and can be tested in the VM, and free QML later keeps the promise that anything can be native. Other options: text only; agent-written QML from the start, with a Python process to start for every card.

**Voice input?** Default: push to talk with local whisper.cpp, in a later milestone. You click a mic button in the pill or hold Super+Space, there is no wake word, and the mic is on only while you talk. A visible button gets found without instructions, and a wake word means a mic that is always on, on a machine where the agent has sudo. This needs a new piece, a small speech daemon, and the VM needs an audio device. Other options: a wake word; the provider's speech API; holding Super alone, which clashes with tapping Super to focus the pill.

**What skips the model?** Default: a small exact list. It covers panel and app names, undo, redo, stop, hide, history, lock, restart and shut down. A fixed alias table adds the names people already know: chrome, google and internet open the browser, and wifi, sound, brightness and battery open the built-in cards. A line starting with "!" runs a shell command directly. Everything else goes to the agent, and local actions are logged so the agent knows about them. Open, stop and undo must be instant and must work offline, and a parser that guesses would give the machine two brains that sometimes disagree. Other options: everything goes through the model; a local intent parser.

**What happens when you type during a turn?** Default: your message waits as a visible grey chip you can remove, and Esc cancels the running turn. There is no second agent in this milestone; background work means jobs such as downloads and builds, not agents running in parallel. Two agents with sudo writing to one machine would make "this", restore points and undo ambiguous. Other options: a silent queue (today); interrupting the running turn; parallel helper agents.

**Any pause before things no restore point can undo?** Default: none. The step turns red on the live line and shows the exact command while it runs, Esc stops it, and the closing line marks it "can't be undone". In the installer, one red button names the disk and what is on it ("Erase Acme SSD (has Windows) and install"), and you do not have to type the disk's name. A countdown is a guard rail under another name, only Claude has a hook that could run one, and matching shell commands with a regex misses scripts anyway. Other options: a 3-second countdown; typing the disk name.

**If a change breaks the display, the shell or the network, does it revert by itself?** Default: yes, but only when a health check actually fails. That means the monitor is gone, the shell has dropped off agentd's socket, or the network worked before the turn and fails twice after it. agentd quietly puts the changed files back, then one line says what it put back and offers "Redo it". If the screen or keyboard is broken, you cannot click Undo. But a "does everything look right?" countdown after every restyle would interrupt the thing people most enjoy doing. Other options: a countdown after any display or network change; Undo and Keep buttons only.

**Should a broken app fix itself?** Default: once for each distinct error. A visible "Fixing Passwords" strip shows while it happens, and Esc stops it. If the same error comes back, the app shows a Fix button instead. The machine should mend its own apps, and one try per error cannot spiral into a crash loop that burns tokens. Other options: always wait for a click on Fix; fix every crash automatically.

**How does the agent know what's on screen?** Default: every prompt carries a short text block (the window, the tab URL, the selection), shown to you as the "this" chip, and the agent takes a screenshot only when it asks for one. Taking screenshots constantly is a mode you turn on by asking, and an eye appears on the dot while it is on. Screenshots all day cost time and tokens, would send your password manager to the provider, and still tell the agent less than the exact URL does. Other options: periodic screenshots always on.

**What does "this" point to?** Default: whatever you pinned with Super+click. If there is no pin, it is the focused window, with its selection and tab URL. The mouse is often parked somewhere random, so reading what is under the cursor would guess wrong. Other options: whatever is under the cursor; the focused window only.

**One conversation or chat threads?** Default: one conversation you never have to manage. agentd keeps the session across reboots. After about 4 hours idle, or when the context gets large, it quietly starts a fresh session seeded with memory.md and the last few turns. Visible threads would turn it back into a chat app, and one endless session slows down the first words of every reply. Other options: one endless session; a "new chat" button; one conversation per app.

**Claude, Codex, or both?** Default: one main provider, which you switch with a sentence. Both load the same memory.md and the same os-mcp, so nothing is lost when you switch. The resting pill says "Ask anything", and the provider is named where it matters: in setup, sign-in, limits, errors and the dot's tooltip. You talk to your computer, not to a vendor, but an honest error has to say which provider signed you out. Other options: "Ask Claude anything", following the active provider; a picker chip in the pill.

**Where do you sign in, on the live USB or after install?** Default: on the live USB, and the sign-in carries into the install along with the conversation and memory. You sign in once, and the agent can then help with the install. Other options: after install; both.

**What does the pill do when an app is in front?** Default: it narrows to about 360px but keeps the dot, "Ask anything", the battery and the clock fully visible. A tiny capsule with only the dot and clock is used just for full-screen video and games. For a new user, those words are the only sign of where to type. Other options: always full width; a small capsule at 60% opacity; hiding the pill.

**How are windows placed?** Default: a stage. One app sits in the middle, and a second goes beside it when you ask or drag it to an edge. "Put that away" sends a window to a shelf above the pill, and typing its name brings it back. Today every app floats in the center at 540x660, stacked with no way to find them, and tiling would make it feel like a developer's desktop. Other options: tiling; free-floating windows with title bars.

**Do windows keep a close button?** Default: there are no title bars, but a small X and a drag strip appear at the top edge when you hover. Alt+F4, Ctrl+Q, Ctrl+W and Alt+Tab keep their usual meaning. Everyone looks for the X on the window itself. Chromium and Nautilus draw their own headers either way. Other options: close only from a chip in the pill; full title bars.

**Is the browser a panel or a window?** Default: it slides in like a panel, then stays until you put it away, works with Alt+Tab and can sit beside an app. The terminal and Files stay as panels that leave when you click the wallpaper. People spend most of their time in the browser, and it would feel broken if a stray click hid it. Other options: all three as panels, as today.

**Do panels slide in from different edges?** Default: one direction for now. Hyprland's special-workspace animation is a single global setting, so sliding each panel from its own edge would mean rebuilding the panels as floating windows or waiting for the custom compositor. Every panel behaving the same way matters more than the direction. Other options: rebuild the panels as floating pinned windows now.

**Where do notifications go, and when does the agent speak up on its own?** Default: a notification is one line in the pill that stays until you have seen it, with a mark on the dot. You accept it with a click or Tab, never with Enter on an empty pill, and afterwards it is filed in the history. mako is removed. The agent speaks up only about watchers you set up and about something breaking, and it never starts work on its own. Everything comes through one voice in one place, and uninvited actions on a machine with no guard rails would wear down trust. Other options: mako popups; a line that vanishes after 8 seconds; an agent that acts on what it notices.

**How do apps share data with the agent and with each other?** Default: now, plain files in ~/Apps/<name>/data, described in app.toml. Later, once the kit's runtime contract settles, each app's backend is exported on D-Bus. Files cost nothing and both providers can read them. D-Bus later adds typed calls and things like logging a run without opening the app, still with no ports. Other options: D-Bus now; messages routed through agentd.

**What do you see while an app is being written?** Default: the skeleton first, which is only a rule in the system prompt: a small window with rows marked "sample" that then fills in. Next comes a window frame that opens with the app's title as soon as the tool call starts. That works on Claude only, because Codex does not stream its tool input. This gives most of the effect for very little work. Other options: nothing until the app is complete; rendering half-written QML.

**Do you ever see the code?** Default: only when you ask in words. "Show me how Passwords works" opens its folder in the terminal panel. Code on screen makes the machine feel like an IDE, and technical users still get there with one sentence. Other options: a key that flips the window over to show the code; code shown beside the app while it builds.

**One CLI process per turn, or one kept alive?** Default: one Claude process kept alive per session through stream-json input, once it has been checked with --resume, the MCP config and bypassPermissions. Codex keeps one process per turn, and starting a process per turn remains the fallback for both. For apps, a spare bombadil-app process waits ready for the next window. Starting a process is most of the wait on a small request. Qt sets the window class once per process, so one shared process cannot host many apps, and one bad backend in it would freeze them all. Other options: a new process every turn (today); calling the Agent SDK directly.

**How many restore points are kept?** Default: every turn gets a pre and post pair. Pairs where nothing changed are dropped at the end of the turn, and the last 100 pairs that did change something are kept. Snapper's cleanup timer is switched on, and the old @.undone-* subvolumes are pruned. Most turns change nothing, and a full disk is a failure you would have to debug yourself. Other options: keep everything; keep the last 50.

**Where does the kernel live?** Default: in /boot on the btrfs root, so restore points include it, with the ESP moved to /efi and GRUB's grubenv kept on the ESP. Today the ESP is mounted at /boot, so rolling back after a kernel update can pair a new kernel with old modules, and the machine may not boot. Other options: keep kernels on the ESP and keep the previous kernel installed; systemd-boot.

**What is the rescue screen when the desktop is broken?** Default: a text page in large type on tty2 with four choices: undo the last change, restart the desktop, talk to Claude here, or open a shell. There is also a panic key while the compositor still runs, and restore points appear in the boot menu later. A text console depends on the fewest things that can break, and Ctrl+Alt+F2 still works when hyprland.lua is broken. Other options: a second, minimal graphical session.

**Should the history show screenshots?** Default: a small thumbnail at the end of every turn, kept on your disk under /home, with apps marked private blurred and a switch to turn thumbnails off. Pictures make the history easy to scan. Blurring does not cover a bank page in the browser, and the setting should say so. Other options: a history with text only.

## Everything else, by moment

### First boot

- **Installing from the live USB is one card.** Once you are signed in on the live USB, a chip offers "Install on this computer". A card lists the disks in plain words ("Acme SSD, 1 TB, has Windows on it"), one red button installs, and the machine restarts signed in, with the same conversation and memory. Copying ~/.claude and ~/.codex into the install is a small change to bombadil-install worth making now. "Install next to Windows" is new installer work, and because the installer copies the live system as it is, anything changed during the live session comes along too.

### First minute

- **A ten-second tour.** Right after sign-in, the browser, the terminal and Files each slide in and out once and name themselves, and then a chip offers "Make me something for what I do". The tour is a fixed script in agentd, so it is the same on both providers, and Esc skips it.
- **The built-in apps can be changed too.** Rewind, System and Settings ship as ordinary Bombadil apps in /usr/share/bombadil/apps. Your first change makes your own copy in ~/Apps, and "reset Settings" brings back the original. Build this together with Rewind.

### Ordinary asks

- **Drop or paste onto the pill.** Drag a PDF or an image onto the pill, or press Ctrl+V, and it becomes a chip ("report.pdf x") that goes along with your words. Whatever it produces lands in a sensible folder, with a Show button. Test drag-and-drop into a layer-shell surface early. Chromium uploads will still open the system file chooser, so style that chooser to match.
- **Point at it** (signature). Super+click a window or drag a box around part of the screen, and a chip names what you pointed at ("Passwords > Delete button", "Region 640x320"). The agent can point back with an orange ring for a few seconds. Cropping a window works for every app and is small. Naming the exact QML element needs a small socket in the app runtime. "Change this app" on the window chip should reach the same thing with a normal click.
- **Talk instead of typing.** Click the mic in the pill or hold Super+Space, and your words appear in grey as you speak; Esc throws them away. This needs a local speech-to-text daemon, which is a new piece.
- **The closing line is the receipt.** When a turn changed something, its last line says so the way you would ("Installed Docker."), with Undo and Details. Details lists the changes in plain groups, with diffs. It needs a snapper pre and post pair for every turn, plus filters on /home so browser churn does not flood the list.
- **Music controls by the clock.** While anything plays, a small play, pause and next chip sits beside the clock. It comes from Quickshell's MPRIS service and needs no work from the agent.

### Something new

- **Watch the app build itself** (signature). The skeleton-first rule ships with piece 3. Next, the moment a build starts, a window frame titled "Passwords" rises from the pill with a soft shimmer and a line counter. That frame works on Claude only, because Codex does not stream tool input. Rendering half-written QML is left out.
- **A broken app mends itself.** When an app hits an error, its window keeps the last working state and shows a strip at the bottom ("Something broke in Passwords. Fixing it."). If the process dies, it restarts once. Each app runs as a systemd user unit, and the Fix button sends a prompt to agentd tagged with the app's name.
- **Keep a card.** A pin in a card's corner, or saying "keep it", turns the card into a named app in ~/Apps that opens by name. Chips in the bar come later, and only from kit components or drawn by the app runtime, never as generated QML inside the shell.
- **Apps that work with the agent and with each other** (signature). A week after you make a habit tracker, "log a 5 km run" logs it without opening the window. Right-clicking a process in the memory viewer can ask "why is this using 4 GB?". Data files and an Agent.ask() call for apps come first; D-Bus calls wait for the kit's runtime contract.

### A week later

- **One conversation and a memory you can read.** The line "Welcome back. Since last time you built Passwords and Tracker." is built locally from turns.jsonl, with no model call, and "what do you know about me?" opens a short list you can edit. One file, ~/.bombadil/memory.md, is linked as ~/.claude/CLAUDE.md and as ~/.codex/AGENTS.md. Do not also add ~/CLAUDE.md, because Claude runs from $HOME and would load the file twice. Saving session_id across reboots is a five-line fix to make now.
- **Rewind** (signature). Super+Z or typing "history" slides up every turn, grouped by day. Each turn shows a small screenshot, your words and what changed, and typing filters by words or by a path. Clicking a turn offers "Undo just this", "Go back to before this" and "Ask about this". Rewind replaces a notification center and a recent-files list, and it should be the first app built with the kit. Keep its data under /home so a rollback never erases it.
- **Every version of an app** (signature). "Show me Passwords from yesterday" opens a slider that steps through each change, labeled by the prompt that made it, and letting go on a version keeps it. "Make a copy for my team" makes a second app. It reads the git history from piece 3.
- **Why is this like this?** (signature). "Why is my DNS set to 1.1.1.1?" gets an answer with the turn that changed it, the time and the old value, plus "Undo just this file". It is cheap once receipts exist, and the agent can answer from turns.jsonl before any index is built.
- **Chips that learn your habits.** After about 20 turns, the example chips become your own most-opened apps and "Continue: <the last thing you did>", ranked locally from turns.jsonl.

### When it goes wrong

- **Provider trouble is one sentence and one button.** You see "Claude signed you out. [Sign in]", "Claude is at its limit until 15:00. [Use Codex until then]" or "Offline. Undo, history, apps and panels still work.", not the last 2000 characters of stderr. CLI wording changes between versions, so keep the error patterns in providers.py with tests built from captured output.
- **Live undo that covers your home folder** (signature). "Undo that" strikes through the changed files one by one and ends with "Undone. 14 files back as before 'install docker'.", with no restart for config and home changes. It leaves alone the files you edited since, lists them, and "redo" puts everything back. This is the largest piece in this list: a snapper config for /home set up by the installer with exclusions, the list of paths that are safe to revert live, and service restarts.
- **The machine puts back a change that cut it off** (signature). If a turn loses the monitor, drops the shell or breaks a network that worked before, agentd puts back just those files without asking and says so afterwards. This needs no snapper: copy the short list of watched files at the start of the turn and restore those copies.
- **Lifeboat.** When the desktop dies, the screen switches to a page in large type on tty2 with four choices, each one key. While the compositor still runs, Super+Ctrl+Backspace opens a plain menu. Move agentd and Quickshell to systemd user services with Restart=always now. Today they are started by hl.exec_cmd and stay dead if they crash.
- **A bad boot rolls itself back** (signature). After two failed boots, the machine starts from the restore point before the turn that broke it and says "Last boot failed after turn 41 (install nvidia-open). I went back. Try another way?". The whole feature is large (grub-btrfs, a boot counter and a boot-ok unit), but one part is urgent now: moving the ESP to /efi.

### Always on

- **The pill at rest.** With nothing in front, the screen shows only the wallpaper and the pill. With an app in front, the pill is narrower but still says "Ask anything". The layer surface should keep a fixed size, with the pill animating inside it and an input mask so the surface stops swallowing clicks meant for the windows behind it.
- **No title bars.** Windows get a thin outline, 12px corners and a soft shadow. The focused app's name shows as a chip in the pill, offering Close, Full screen, Change this app and Show its files. Nautilus and Chromium draw their own headers, so this look applies to Bombadil apps and the terminal.
- **The stage and the shelf.** Apps you put away shrink into a row above the pill that appears when you tap Super, and typing a name brings one back where it was. A hidden special workspace holds these windows, and a place() tool arranges windows on request. Live thumbnails of hidden windows may not render, so icons are the fallback.
- **Background jobs as chips.** "Download the Ubuntu ISO and check it" ends at once with "Started." and a chip to the left of the pill ("ISO 43%"). The chip turns green when the job is done, or red with "Why?" if it fails. Jobs run through systemd-run --user and never take over the screen.
- **Watchers.** "Tell me when the download finishes" or "every morning, tell me which updates matter" becomes a systemd user timer that speaks through the pill. "What are you watching?" shows each watcher with a stop button.
- **Windows that open fast.** A spare bombadil-app process, with Qt and the kit already loaded, waits for the next app or card, so a window appears a few hundred milliseconds after it is written.

### Cut, and why

- **Learn undo by breaking something (the pink screen).** The first minute should show what the machine can do, and the live USB has no restore points to undo. Instead, the first real change gets a one-time "Changed your mind? Say undo."
- **Flip an app over to see its code.** It makes the machine feel like an IDE, and "show me how this works" opening the folder covers the same need.
- **A three-second countdown before one-way acts.** It is a guard rail by another name, it would work on Claude only, and a regex over shell commands misses scripts anyway. A red line and "can't be undone" are just as honest.
- **"Put that there" (talking while pointing).** It is large, research-grade, and depends on both voice and pointing. Revisit it once those ship.
- **Make this web page a native app with a CDP pick tool.** Capturing the page's data means reloading the page with network logging on. "Make this page an app" through "this" covers most of the idea, and the rest can come from the planned browser tools.
- **Generated bar chips loaded inside the shell.** Agent-written QML inside Quickshell can freeze the only way to talk to the machine.
- **Parallel helper agents (Shift+Enter, a Codex second opinion).** Two agents with sudo on one machine break the model of one turn and one restore point, which undo depends on.
- **Keys 1-5 on the starter chips.** They steal the first character of prompts such as "10 minute timer".
- **Building a first app automatically during the tour.** A slow or weak first app is the worst possible first impression, so it becomes a chip you can press instead.
- **Holding Super+Escape to freeze a turn.** It is a second meaning on the stop key that nobody would find, and stop plus undo already cover it.

## Gaps worth thinking about

### In the code today

- "Undo that" sent through the agent undoes nothing. The undo turn takes a snapshot of itself first, and rollback then picks that snapshot (undo_last_turn in snapshots.py).
- agentd reads the whole CLI output before showing any of it (the loop in AgentD.turn), so during a long turn the bar shows only a pulsing dot, then everything at once.
- The turn counter restarts at 1 every time agentd starts, so snapshot names repeat after each reboot. Use a lasting id taken from turns.jsonl.
- stderr is read only after the CLI exits, so a CLI that writes a lot to stderr can hang a turn forever.
- A Codex resume rebuilds the command without the os-mcp, instructions, model and -C flags, so from the second Codex turn on, the OS tools and the system prompt may be missing. Also check whether -c instructions= replaces Codex's base instructions instead of adding to them.
- session_id lives only in memory, so every reboot, including the one after an undo, starts a blank conversation.
- The installer mounts the ESP at /boot, so kernels are not in snapshots. Move the ESP to /efi and keep grubenv there.
- Snapshots are never cleaned up. Nothing sets a cleanup algorithm when snapshots are created, snapper-cleanup.timer is off, and @.undone-* subvolumes pile up.
- Chromium starts without --user-data-dir, so CDP on port 9222 probably does not work, and /json/new also needs a PUT request.
- The bar is a full-width transparent window that swallows clicks above the pill as the reply grows. Give it an input mask.
- app.py is never hot reloaded, because the backend is built once per process.
- agentd and Quickshell do not come back after a crash, and bombadil-setup even kills agentd and relaunches it through hyprctl.
- The CLIs are installed with npm under /usr, so they cannot update themselves as the user, and a root rollback silently downgrades them. Package them in the image and set DISABLE_AUTOUPDATER.
- With several monitors, shell.qml makes an independent pill on each screen, and nothing defines which one Super focuses.
- Keybinds already collide: Super+Escape restarts Quickshell, Super+Q closes windows, and a Super-tap bind would also fire after Super+click. All binds need one table.

### Not designed yet

- The agent driving the browser while you watch. The panel slides in, a highlight shows what it clicks and types, you grab the mouse to take over, and you say "continue" to hand control back.
- A speed budget with a benchmark. A fixed set of prompts on both providers would time Enter to first pixel, to a card and to an app skeleton. Run it on KVM or real hardware, because the TCG VM cannot measure it, and add a fake provider that replays captured stream-json so every UX state can be tested without API cost.
- Choosing the model for each turn: a fast model for one-line answers, cards and controls, and the large one for building apps, picked by agentd with --model.
- One live theme for the whole machine (the shell, cards, apps, GTK apps such as Nautilus, and Chromium), so "make everything warmer" restyles everything at once.
- A motion and sound language: how things rise from the pill, slide in and go back, plus an optional soft sound when a turn ends.
- Laptop basics that work without the agent. That means brightness, volume, media and mic keys with a level shown in the pill, Print Screen, suspend on lid close, battery, tap-to-click and HiDPI. It also means a status card opened by a click on the clock, with Wi-Fi, Bluetooth, sound, battery, Sleep, Restart and Shut down.
- Lock screen and encryption: lock on lid close and when idle, a user password, and full-disk encryption offered at install. The agent has sudo and your accounts are signed in.
- Familiar keys keep their meaning (Alt+Tab, Ctrl+C and Ctrl+V, Alt+F4, Print Screen), and hints show a Windows-key symbol instead of the word Super.
- A shape for the rare question the agent really must ask ("which network?"): two or three buttons in the pill, never a paragraph.
- Ordinary Linux apps: how "install Spotify" or "open GIMP" works (pacman, the AUR or Flatpak), how these apps land on the stage, open by name and follow the theme.
- Week-one basics: codecs, PDFs and images, Bluetooth headphones, external monitors, and the webcam, mic and screen sharing in Chromium (xdg-desktop-portal-hyprland with PipeWire).
- Updates on rolling Arch as a background job inside a snapshot, reported as one line about what changed and what needs a restart, never as a terminal.
- Memory cost: one PySide6 process per app adds up, so decide how many stay running, and add "what's running" as a card.
- Account and usage: setup should say which plan each provider needs, and a gentle usage hint should warn before the limit rather than after it is hit.
- Accessibility: text size, reduced motion, a screen reader and high contrast. "Make everything bigger" should be one of the known controls.
- Bringing your stuff over: Chromium sign-in and sync must keep working under the managed policy, and bookmarks and passwords should import from an old computer.
- The app skill and the component kit must be served through os-mcp (app_template, app_guide) rather than as a Claude-only skill, so that Codex builds apps as well as Claude does.
- Exclusions for home snapshots must be decided at install time: ~/.cache, ~/.claude, ~/.codex, ~/.local/state/bombadil and the Chromium profile. Otherwise undo rewinds the conversation itself, and every receipt fills with browser churn.
- The live USB has no snapshots at all, so the live session must say that undo starts after install.

## What would make it feel like a chat window instead

- A reply box that grows into a scrolling log with markdown, avatars and history. Keep only the current turn there; the past belongs in Rewind.
- Silence after Enter, and simple lookups like "open the browser" that wait several seconds for the model.
- Internals on screen: tool names such as mcp__bombadil-os__create_app, gear icons, raw JSON, or the last 2000 characters of stderr, which is what the bar shows today. Chatty lines such as "Hmm, let me think!" are no better.
- The agent explaining how to do something, or asking follow-up questions, instead of doing it, showing the result and offering to adjust.
- A "new chat" button, a list of threads or a provider picker. One machine has one conversation.
- Popups, toasts and "Are you sure?" dialogs over your work. Status belongs in the pill, and any check happens after the fact.
- Every answer becoming a card or a new window. An answer that is a single number is a sentence, and an app that already does the job gets extended rather than copied.
- Making things "native" with a web UI: a settings page in Chromium, a localhost port or HTML charts.
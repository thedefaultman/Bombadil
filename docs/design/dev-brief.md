# Building on Bombadil

> **Status:** In progress. Pieces 1 and 2 have code that is not on `main`. Piece 3 has only its plain own copy and the `BOMBADIL_SESSION` gate in that code (they come with piece 1); the rest of piece 3 and pieces 4 to 6 are designed and have no code.  
> **Code:** none of the six pieces is on `main`. They change `src/bombadil/agentd.py`, `src/bombadil/launcher.py`, `src/bombadil/mcp_server.py`, `bin/bombadil` and `iso/airootfs/etc/skel/.config/hypr/hyprland.lua`, and build on `src/bombadil/procs.py`, `src/bombadil/snapshots.py`, `shell/PillState.qml` and `shell/QueueChips.qml`, which they leave as they are (a new `shell/SessionChips.qml` is planned beside `QueueChips.qml`). Where each piece stands is kept in the roadmap and the coding-sessions architecture page.  
> **Design:** [Building on Bombadil, the page](pages/building-on-bombadil.html)  
> **Decided by:** the project owner, 2026-09-27  
> **Verified:** 2026-10-01 against `main` at `26843d3`: the status above and the list "In the code today", where on `main` a foot window opens from the `SUPER + Return` binding (`hl.dsp.exec_cmd`) or the terminal panel, and `hl.exec_cmd` starts `agentd` and `bombadil-shell`. The vendor facts under "Checked today" are dated 27 Sep 2026 and were not re-checked.

You tap Super and type "claude acme". A terminal rises with the real Claude Code already in that project, and you work in it exactly as you do today. Close the window and nothing stops. Beside the pill, one small chip per project keeps a dot for each session: moving while it works, lit when it is your turn, red when it failed. Type a name and that session comes back mid-sentence. Each session runs as you, without sudo, in the checkout or in its own copy when another session already has it, with its own ports, so the pill's one conversation and its undo stay exactly as you confirmed them. When a session needs the system changed, it asks the pill and one key says yes. The vendors' own Linux apps install like any app, but the flow is the terminal on the desk and the Claude app on the phone, which buzzes only when it is your turn. No new keys. You learn a few words: the names you give things, "end", "ship" and "undo this".

This is the second experience brief. It sits on top of the first one ([How Bombadil should feel](ux-brief.md), 27 Sep 2026), whose 29 decisions are confirmed and stay in force. Where development work needs one of them extended, the change is named below. The project owner confirmed this brief and its 19 defaults on 27 Sep 2026.

## The rules

1. **The coding tools run exactly as shipped, and Bombadil hosts them.** Claude Code and Codex improve every week for free, a home-made front end would fall behind within a month, and Anthropic restricts third-party front ends on a Claude subscription login. What an OS can add is everything around them: keeping them alive, placing them, seeing them all at once, fencing them, and undoing them.
2. **Closing a window never ends a session Bombadil started. Only "end" does.** Sessions run for hours and a window is only a view of one, so a stray Alt+F4 must cost nothing.
3. **A coding session is a program you run, not a second voice of the machine.** It runs as you, without sudo, in the checkout or its own copy of the code, with git as its undo. That keeps the pill's one conversation whole, and its rule of one turn, one restore point.
4. **The pill is the one list, and it speaks only when it is your turn, something failed or a limit is near, never about the session in front of you.** With several sessions running, your attention runs out long before the machine does, and only the OS can see Claude, Codex and plain shells together.
5. **Everything comes back by name.** Projects and roles are words you type into the pill, like app names, so there are no new keys, only names and a few verbs.
6. **Every session gets its own room: the checkout or its own copy of the code, ten ports, a database name, a browser profile and a memory limit.** State that lives outside git is what breaks parallel work most often.
7. **Nothing is pushed, opened as a pull request or merged until you say "ship" or "land".** A push reaches other people, where no undo can follow.

## Build these first

Six pieces, in this order. The first two are useful on day one with the sessions a person already runs. The third is what lets parallel sessions exist without breaking the confirmed undo model, and everything after it reads from the first three. Before any of this: merge the status-line and app-kit changes (both edit shell.qml and the system prompt, and both are on main as of 2026-10-01), and move agentd and Quickshell to systemd user services, which the first brief already asks for.

### 1. A session outlives its window and comes back by name

**What you see:** Tap Super, type "claude acme" and press Enter. Within a fifth of a second a large window rises, and a moment later the unchanged Claude Code TUI is in it, already in the Acme folder with that project's tools on the path. "codex bombadil" does the same with Codex, and "claude bombadil reviewer" names the session's role. A second session on a repository that already has one gets its own copy of the code, and the line says "Started reviewer on Bombadil in its own copy." Alt+F4 or the hover X only closes the viewer; the session runs on and its dot stays. Typing "reviewer" brings it back exactly as it was, cursor and scrollback included. After a reboot the dots come back dim, and clicking one resumes that conversation where it stopped; nothing resumes on its own after a restart.

**Why first:** Everything else needs sessions that Bombadil knows about and that do not die with a window. Today a coding session is a bare foot window: it dies when its window closes or the compositor crashes, because everything starts from hl.exec_cmd in hyprland.lua, and a session the agent started for you dies on Stop, which kills anything in the turn's process tree that is not in PROTECTED_ARGS (procs.py).

**What changes:** A new dev.py in agentd, with a registry file at ~/.local/state/bombadil/dev/sessions.json (name, tool, session id, folder, unit, state). Each session runs in its own systemd user scope under bombadil-dev-<project>.slice, wrapping an invisible zellij session (locked, keys cleared, no bars, serialization on) that runs `mise exec -- claude -n "<role> on <project>"` or `mise exec -- codex --no-daemon`, so the vendor's own session list shows the same names as the dots. The foot window is only a viewer (`footclient --app-id=bombadil-session --toplevel-tag=<project>/<role> zellij attach`, with `foot --server` as a socket-activated user unit), launched straight onto the stage with hl.dsp.exec_cmd, opened large and clear of the pill. Resume runs `claude --resume <id>` from the same folder or `codex resume <id>`. The launcher's exact list (launcher.py) gains project names under ~/Projects, role names, and the fixed forms `claude|codex|shell <project> [role]`. Sessions you start by hand in a plain terminal are seen through the hooks of piece 2 and listed as "yours". Before building: a one-day spike in the VM that zellij passes Shift+Enter (the kitty keyboard protocol), the mouse and OSC 9/99/777 notifications through to foot, with tmux (status off, passthrough on, extended keys) as the fallback host.

**Effort:** L

### 2. Dots that tell you whose turn it is

**What you see:** Left of the pill, one small chip per project, with a dot for each session. A dot moves while its session works, sits lit when it is your turn (it asked something, or finished and you have not looked), turns red when it failed, and dims when it is asleep. When a dot lights up while you are elsewhere, one line appears in the pill ("reviewer on Bombadil: run the migration?") and Tab takes you there; several at once merge into one line ("api and reviewer are waiting"), and pressing Tab again walks to the next one, ending with "Nothing needs you." Tab walks the your-turn lines first; a session's request for the machine ([Do it], piece 3) is always the last stop and never merges into the walk. The session in front of you never announces itself. Hovering a dot shows its last few plain lines without opening it. "what's running?" opens a card with one row per session: tool, state, since when, last sentence, files changed, memory. Above 80% of a usage limit, one line says "Claude 5-hour limit at 84%, resets 15:00." With more than three projects the chips fold into one ("4 projects · 1 waiting").

**Why first:** One list across Claude, Codex and shells, quiet about whatever you are already watching, is the thing neither vendor can build, and it helps on day one with sessions started by hand.

**What changes:** A managed drop-in at /etc/claude-code/managed-settings.d/50-bombadil.json adds async command hooks for session start and end, each prompt, permission requests, the AskUserQuestion pre-hook, stop, failure and folder change, all running `bombadil-signal`, a tiny program writing to agentd's socket. The immediate "your turn" signal comes from PermissionRequest and the AskUserQuestion pre-hook, because Notification's permission_prompt fires about six seconds late and idle_prompt about a minute late. Every hook drops events carrying BOMBADIL_OS=1, because managed hooks also fire inside agentd's own turns. While any background Claude session exists, `claude agents --json --all` reconciles the states. Codex gets the same events as managed hooks in /etc/codex/requirements.toml (SessionStart, UserPromptSubmit, PermissionRequest, Stop, Interrupt, SessionEnd; trusted by policy, so no trust prompt), plus `notify` for agent-turn-complete in the skel config. Plain shells and test runs report through foot's [desktop-notifications] command with inhibit-when-focused off, and Hyprland's bell and urgent events are the last fallback. agentd follows activewindowv2 to know which window is in front, and broadcasts a new dev event family that carries no turn id, so PillState's single-turn logic is untouched; SessionChips.qml sits beside QueueChips.qml, and turns.jsonl gets kind:"dev" rows so Rewind shows session starts and ends. Claude's meter comes from a default status-line command in skel that forwards rate_limits to agentd; Codex has no supported read yet, so its limit shows only when a session hits it.

**Effort:** M

### 3. The fence: no sudo, an own copy, and one door to the machine

**What you see:** A coding agent that runs `sudo pacman -S postgresql` gets a plain refusal in its terminal, and its instructions tell it to run `bombadil ask "install postgresql 17"` instead. The pill then shows "api on Acme asks: install postgresql 17 [Do it]". Tab runs it as a normal machine turn, with the amber line, a restore point and Undo, and the session gets the one-line receipt back; removing the chip sends it "refused". Esc in the pill and Super+Escape never touch a coding session. Two sessions on one repository never see each other's edits, each has its own ports, and a runaway build cannot freeze the pill.

**Why first:** With passwordless sudo, a session's root change lands inside whatever machine turn happens to be open, so undo silently reverts it or misses it, because only agentd takes turn snapshots (snapshots.py covers the root config only). Stop kills any process in the turn's tree and can delete the pacman lock while another session is using it. A fence is much harder to add once sessions already exist.

**What changes:** Each session's scope starts through `setpriv --no-new-privs`, so setuid sudo fails inside it; rootless podman needs a user podman.socket outside the fence, exported as DOCKER_HOST. A `bombadil-worktree` script makes each further session's copy at ~/Projects/.work/<repo>/<role> with `git worktree add`, copies the files named in .worktreeinclude, and reflinks node_modules, target and .venv (nearly free on btrfs); Claude's WorktreeCreate hook calls the same script, so `-w` lands there too. A session backgrounded with /bg already sits in a Bombadil copy, so worktree.bgIsolation is none, and by Claude's own rule it then asks before committing. Each scope gets ten ports from 4000 to 4999 as PORT and BOMBADIL_PORTS, a COMPOSE_PROJECT_NAME and a BOMBADIL_DB name, and bombadil-dev.slice gets MemoryHigh, MemoryMax and a lower CPU weight than the desktop, plus zram. procs.Stopper never enters the slice. The installer makes ~/Projects a nested subvolume, outside home snapshots. `bombadil ask` is the command-line face of the ask_machine tool from piece 5: it sends a request message, and on Tab that becomes a prompt marked "[asked by coding session api on Acme, untrusted]", the same pattern as the app kit's Agent.ask. The developer note (you have no sudo, use `bombadil ask`; your ports and database name; commit on your branch, Bombadil pushes when the person says ship; report progress with the progress tool) reaches coding sessions only, gated by BOMBADIL_SESSION=1, which dev.py sets in the session's environment, never by folder: Claude through SessionStart additionalContext, Codex through developer_instructions in a dev profile. memory.md stays about the person. The OS agent's own session sets crossSessionInbound to refuse, because a bypass-mode Claude session delivers messages from other bypass sessions by default, which would be an injection path into the agent that has sudo.

**Effort:** L

### 4. Undo per session

**What you see:** With builder's window in front, tap Super and type "undo this". The line says "Undone. 2 files in builder on Bombadil back as before 'drop the old cleanup'. README.md skipped, you changed it since." "undo builder" works from anywhere, and "redo" puts it back. A bare "undo" still means what it always meant: the machine's last turn, and it never touches your projects; with a session in front, its line says what it undid on the machine and adds "builder untouched; say undo this for it." The session's next prompt carries a note saying what you undid, and the vendors' own Esc Esc rewind keeps working inside the terminal.

**Why first:** Parallel agents without a cheap way back are how people lose an afternoon. Today nothing under ~ can be undone, and Claude's own rewind misses changes made through the shell.

**What changes:** On both vendors, the UserPromptSubmit and Stop hooks run `bombadil-checkpoint pre|post`: `git add -A` into a persistent per-role index file (.git/bombadil/<role>.index, so it stays fast on big repositories), then write-tree and commit-tree, saved as refs/bombadil/<role>/<n>/pre and post. HEAD, the branch and the working tree are never touched, and the post step runs asynchronously. Undo restores each file whose content still matches its post state and lists the rest, which is the first brief's own rule for home undo. Ignored files (node_modules, databases) are not covered, and the line says so. The launcher's _undo (launcher.py) handles "undo this" and "undo <name>" locally, with the session named by the "this" chip or the word. Checkpoints are kept for 7 days after a session ends.

**Effort:** M

### 5. Handing words across, at the desk and from the phone

**What you see:** "tell api the tests need Postgres 17" types those words into api's terminal, marked "(from the pill)", and sends them while you watch; if you are typing in that window it waits. With a GitHub issue open in the browser, "give this to claude on bombadil" starts a session with your words and the issue's URL, exactly as typed, and the line says "Started issue-97 on Bombadil." "have codex review builder" starts a read-only Codex session on builder's code as a dot, and its notes come back as a your-turn line. After a break, the line says "While you were away: builder finished (4 files), reviewer is waiting, batch at 212 of 300." On the phone, the Claude app lists every Claude session under the same names, and it buzzes only while you are away from the desk.

**Why first:** Handing work between the machine and the coding tools is the reason to host them in the OS at all, and the person reads on their phone.

**What changes:** os-mcp gains list_sessions, start_session(project, tool, role, prompt, read_only), tell_session(name, text), session_screen(name, lines), stop_session and end_session, which the OS agent uses only when asked. Words reach a session through `zellij action paste --pane-id` plus Enter, which works the same for any TUI of either vendor; Claude's inbox socket is not used, because posting into it from outside is undocumented. Sessions get a small bombadil-dev MCP server instead of os-mcp, with ask_machine, progress and show_preview, so a session can never call rollback or create_app; it is passed per launch with --mcp-config, never through managed-mcp.json, which takes exclusive control of MCP servers. User settings turn on remoteControlAtStartup, which applies to interactive sessions only, so agentd's headless session never goes to the phone, and CLAUDE_CLIENT_PRESENCE_FILE points at a file that a hypridle listener creates on activity and removes on idle or lock. "While you were away" is built locally from the registry, with no model call. Codex has no phone host on Linux, so a Codex session's question waits in the pill.

**Effort:** S to M

### 6. Try it, read the change, ship it

**What you see:** With api in front, "run it" starts the project's dev server as a dot ("api :4012") and opens the page beside the session in its own small browser window. "what did builder change?" answers "7 files, +240 −60, mostly the snapshot cleanup" with a Diff button that opens the diff in a terminal beside the session; when two live sessions changed the same file, the line adds "also changed by kit: shell.qml". If the project names a check, it runs in the background after each turn, its result shows on the dot and in the hover peek, and a new failure goes back to the session once, marked "(from Bombadil check)"; the same failure twice turns the dot red. "ship builder" tells builder to commit, then Bombadil pushes and opens the pull request, and the dot reads "PR 418". After the merge the dot reads "merged · Clean up", and "clean up" removes the copies that merged and are clean. "land builder" merges locally when there is no remote.

**Why first:** Review, not generation, is the bottleneck of parallel work, and ports and previews are where sessions collide. Catching two roles heading for the same file at minute five saves an afternoon at merge time.

**What changes:** The run and check commands come from mise.toml tasks, then package.json, Cargo.toml or pyproject.toml, and are remembered in ~/.local/state/bombadil/projects/<p>.toml, never written into the repository. "run it" is a systemd-run job with the session's PORT; the preview is `chromium --app=http://localhost:$PORT` with its own user-data-dir and debugging port, never the browser panel's profile or port 9222. The diff is `git diff $(git merge-base main HEAD)` in `footclient --app-id=bombadil-diff`. Overlap marks compare the changed paths of live sessions' checkpoints, plus `git merge-tree --write-tree` against main, all locally with no model call. Checks are opt-in per project, run as a job inside the session's slice after Stop, never inside the hook, and retry once per distinct error, the same rule the first brief confirmed for broken apps. Shipping runs gh pr create and gh pr view from the registry, with BOMBADIL_SHIP=1 set only for that push; landing is a local git merge. A pre-push hook set through core.hooksPath in every Bombadil-made copy refuses any other push, and the one git line (commit on your branch, Bombadil pushes when the person says ship) sits in the developer note, in /etc/claude-code/CLAUDE.md and in the task text of a session started with a task, the three places Claude's docs say a background session reads its git instructions from. The registry, never the model, removes a copy, only on "clean up" and only when it is merged and clean.

**Effort:** M

## A working day

This is how a day should feel once the six pieces above and the best of the rest are built. Most of it does not exist yet.

**07:40, in bed.** The Claude app on the phone shows that batch on Bombadil finished overnight and that api on Acme is waiting on a question. They answer "yes, use the new table" from the phone.

**09:05, at the desk.** They unlock, and the line says "While you were away: batch finished, 300 done, 20 failed. api is working again." Two chips sit beside the pill, Bombadil with three dots and Acme with one.

**09:10, opening a project.** Super, "acme", Enter. A small card names the project, lists its sessions and offers "Claude here", "Codex here" and "Terminal here". Tab on api, and its Claude Code terminal rises large in the middle, where the phone left it: the ordinary TUI, with their own keys, slash commands and status line.

**09:20, starting in parallel.** Super, "codex bombadil: fix the flaky snapshot test". The line says "Started flaky-snapshot on Bombadil in its own copy." and its dot starts moving; they stay on Acme, because a session given a task works out of sight. "claude bombadil reviewer" comes with no task, so it opens in front, resumed from yesterday.

**10:30, switching.** They are mid-sentence in reviewer when the line says "api on Acme: apply the migration to the local database?" They finish their sentence, tap Super, then Tab, and api is in front with the question under the cursor. They answer in the terminal, then type "reviewer" to go back. Alt+Tab works too.

**11:15, the system.** The line says "builder on Bombadil asks: install qemu-full [Do it]". Tab. "Installing qemu-full" shows in amber, then "Installed qemu-full. [Undo]", and builder carries on with the receipt.

**12:00, review and test.** "what did flaky-snapshot change?" gets "2 files, +18 −4: a retry around snapper and a longer timeout. [Diff]". They read the diff beside the session and say "tell flaky-snapshot no timeouts, fix the race". On Acme, "run it" puts api's server on :4012 with a small browser window beside it, and "have codex review api" starts a read-only reviewer as a dot.

**12:40, the machine meanwhile.** "the wifi keeps dropping" is an ordinary pill turn with its own restore point. The coding sessions keep going, and Esc would stop only that turn.

**13:00, undo.** builder deleted a helper it needed. With builder in front, they tap Super and type "undo this". The line says "Undone. 2 files in builder on Bombadil back as before 'drop the old cleanup'."

**13:30, lunch.** The screen locks, and five minutes later the phone buzzes once: "builder finished." They open the Bombadil conversation in the Claude app, read builder's summary and answer "looks right, commit it". Shipping is a pill word, so it waits for the desk.

**14:30, back.** "ship builder", and the dot reads "PR 418".

**15:00, pressure.** The line says "Coding sessions are near their memory limit; batch uses 5.8 GB." Later: "Claude 5-hour limit at 85%, resets 17:00." They start the next batch on Codex with "codex bombadil batch: rerun the 20 failures with a 10 minute timeout", then "when batch is done, tell reviewer to summarize the failures", which the pill lists under what it is watching.

**18:30, end of day.** builder's dot reads "merged · Clean up". "end flaky-snapshot" answers "Ended. Its copy is kept: 2 files not committed." "clean up" removes the copies whose pull requests merged. They shut down for the night anyway, and the line says "batch stops at 14 of 20 and can continue tomorrow."

**Next morning.** The dots are dim. One reads "batch · stopped by restart at 14 of 20 · Continue". They click Continue, and nothing else starts until they ask.

## Terminal or native

**Stays a real terminal:** the Claude Code and Codex TUIs, unchanged, with every slash command, plan mode, rewind and subagent they ship; plain shells; build, test, server and batch output; git, diffs and any editor you start; logs.

**Becomes native, in the pill and its cards:** the dots and the your-turn line; the "what's running?" card and the project card; requests from sessions for the system; usage and memory warnings and "while you were away"; starting, switching, telling, undoing and ending sessions by name; where windows go, and the preview window.

**The line:** if a program waits for your keystrokes, it is a terminal. If it spans sessions, uses the machine's resources, or asks you to decide, it is native, because only the OS sees all of it. The vendors' desktop apps are guests, not part of the flow.

## Decisions I picked a default for

Each of these forks. I picked a default and say why; the other options are listed for the record. The project owner agreed with the findings and accepted every default on 2026-09-27, so these are confirmed decisions, not proposals.

**How do parallel coding sessions fit "one conversation, no parallel agents, one turn one restore point"?** Default: coding sessions are programs you run, not agents of the machine. Each runs a vendor's tool as you, without sudo, in the checkout or its own copy of the code, with git checkpoints as its undo, and the pill reports them as jobs and never queues their prompts. The pill's single conversation keeps the root and your home, so "one turn, one restore point" holds in each place. The first brief cut helper agents inside the machine's conversation because two agents with sudo on one machine make "this", restore points and undo ambiguous; the fence removes both halves of that, and the cut stands for the conversation itself. Other options: coding work as turns inside the one conversation (far too slow); sessions with sudo and a snapper pair per session (interleaved timelines).

**What keeps a session alive?** Default: an invisible zellij session for each coding session, inside its own systemd scope, the same for Claude, Codex and plain shells. It can be read (dump-screen, subscribe) and typed into (paste) by name, and it survives a closed window, a compositor crash and an agentd restart. The vendors' own hosts are observed, not relied on: Claude's supervisor is a research preview that stops a waiting session after about an hour unattached unless pinned, and its background sessions commit and push on their own; Codex's shared daemon is experimental, keeps one environment for every client, and restarts itself for updates (60 seconds' grace, resuming interrupted work since 0.156) unless its updater is off. Sessions you background with /bg are placed in the same fence and pinned. Other options: tmux with its status bar off (the fallback if zellij swallows keys); the vendors' supervisors alone.

**What does "undo" mean now?** Default: a bare "undo" keeps its one meaning, the machine's last turn, and it never touches ~/Projects. "undo this" with a session in front, or "undo <name>" from anywhere, puts that session's last turn back from its checkpoint, live, skipping files you edited since and saying what it covered. It never rewrites history, and the vendors' own rewind keeps working inside the terminal. A word whose target depends on which window happened to be focused is how the wrong thing gets undone at two in the afternoon, and "this" is already how the machine names what is in front of you. Other options: a bare undo that follows focus; the vendors' rewind only (Claude's misses shell edits, Codex removed thread rollback); a snapshot of ~/Projects per prompt (one timeline shared by every session).

**Do coding sessions get sudo?** Default: no. no-new-privileges makes sudo fail inside the fence, and `bombadil ask` turns a request into a machine turn after one tap, with a restore point and Undo. That tap is the only request that ever comes from an agent rather than from you, and it stays a tap: a shim that queues the session's root command to run on its own would let any session, including a prompt-injected one, change the machine through the pill. Sessions you start by hand in a plain terminal keep your sudo and are listed as "yours"; snap-pac records their pacman runs, and the machine's undo does not cover them. Other options: sudo with a snapper pair around each call; a separate Unix user per session; a password prompt (which nobody answers at night).

**Should Claude and Codex work the same way?** Default: yes. The same hosting, dots, checkpoints, ports, handoff, undo and names, launched by the same code with each vendor's flags. The tool is whichever you name, otherwise the last one used on that project, otherwise the machine's main provider. Each tool's own permission mode stays your setting. Two differences remain: only Claude reaches the phone on Linux, and Codex runs with --no-daemon. Other options: Claude only; a picker in the pill (the first brief rules it out).

**Are the vendors' Linux apps used?** Default: they are guests, not the flow. The Codex app (the ChatGPT app for Linux) is officially supported on Arch through OpenAI's own pacman repository, so "install the Codex app" is an ordinary machine turn with a restore point, because its installer adds a third-party repository and runs a full system upgrade. It runs under XWayland, its worktree root is pointed at ~/Projects/.work so its copies get undo, and its threads should show as dots through the same managed hooks (to verify). Claude Desktop for Linux is a Debian-only beta whose docs tell Arch users to run the CLI, so on Bombadil Claude's second surface is the phone app, reached through Remote Control. Neither app sees the other vendor's sessions or terminal sessions, which is why the pill stays the one list. Other options: preinstall both; route work through them.

**When does a session get its own copy of the code?** Default: the first session on a repository works in your normal checkout, and each further session gets a Bombadil copy at ~/Projects/.work/<repo>/<role>, with reflinked build folders, ready in about a second. Bombadil makes the copies and removes them on "clean up", only when clean and merged, never the model. "reviewer works in the main folder" opts a session out. Other options: always a copy, even the first; each vendor's own worktree folder (Codex's sits inside ~/.codex, which home snapshots exclude; Claude's sits inside the repo where tests and search tools scan it).

**Where do windows go?** Default: the single stage the first brief confirmed. Typing a name brings that session to the middle, the previous one goes to the shelf, and a diff, preview or second session sits beside it when you ask. Typing a project's name opens a small project card, not a workspace. Session windows are stage windows like apps: closing one only detaches. Other options: a named Hyprland workspace per project (a second concept beside the stage, revisit if the shelf gets crowded); tabbed groups (a tiling feature); a grid.

**When does a session speak?** Default: only when it is your turn (it asked something, or it finished and you have not looked), it failed, or a usage or memory limit is near, and never for the session in front of you. It is one line in the pill that stays until you have seen it, several merge into one, Tab takes you there, and the phone buzzes only while you are away. Working sessions are silent. Hooks cannot reliably tell a closing question from a finished turn, so "your turn" is one state. Other options: a line at the end of every turn; a sound; badges that never clear.

**Who commits, pushes and merges?** Default: sessions commit to their own branch as they normally do. Nothing is pushed, opened as a pull request or merged until you say "ship" or "land": "ship" tells the session to commit, then Bombadil pushes and opens the pull request; "land" merges locally. A pre-push hook in every Bombadil copy enforces it, and the git line sits in the developer note, the managed CLAUDE.md and the task text, the places Claude's docs say a background session reads its git instructions from. Other options: the vendor default (Claude's background sessions push on their own); a Bombadil commit per prompt.

**How do you talk to a session?** Default: in its own window, as today. From the pill, only an explicit "tell <name> …" reaches a session, and it appears as typed text you can see. The pill never forwards what you type to the focused session, because one box with two meanings is how a request ends up with the wrong agent, and it never rewrites your ask into a brief: "give this to claude on bombadil" sends your words and the "this" context as they are. Other options: the pill sends typed text to the session in front; a model-written brief with "just my words" to opt out.

**How do sessions start?** Default: two ways. The exact forms `claude|codex <project> [role]` with no model call open the session in front, ready to type into. A sentence to the pill with a task ("codex bombadil: fix the flaky test") goes through the machine, which starts the session out of sight, reports "Started flaky-snapshot on Bombadil", and never opens a window by itself. The difference is visible in what you typed: a name gets a window, a task gets a dot. Other options: always open a window; always a dot.

**What do Esc and Stop reach?** Default: Esc in the pill and Super+Escape stop the machine's turn only. Esc inside a session's window is that tool's own stop. "stop <name>" interrupts a session's turn and keeps the session, "end <name>" ends it and keeps its copy, and "stop everything" interrupts every session. A panic key that kills a four-hour batch run is worse than no key. This is a new exception to the one stop key the first brief teaches, and it is named as such. Other options: Super+Escape stops everything.

**What happens to a batch when the machine reboots or a limit hits?** Default: nothing restarts on its own. After a reboot the dot reads "stopped by restart at 14 of 20 · Continue", and Continue resumes the session by id. A session at its usage limit reads "Paused until 15:00" and never counts as your turn; Claude's own auto-resume at the reset is left on. Progress numbers come from the session calling the small progress tool, so batch work should be written as resumable steps with a progress file. The limit meters are per account; per-session token counts would need OpenTelemetry, which is not built. Other options: resume automatically; hand the batch to the other vendor at a limit (a conversation does not transfer, so "start a Codex session from this one's notes" comes later).

**How do the CLIs install and update?** Default: user-level native installs seeded from the image, with every self-updater off. Bombadil runs the updates itself, only when no session is working, inside a receipt, then `claude respawn --all` for sessions backgrounded with /bg; a session in a pane keeps the old version until it ends. You still get the weekly improvements, no batch run is killed mid-way, and a root undo cannot downgrade them. Claude Code does not officially list Arch (it ships apt, dnf and apk repositories), so the native installer is the channel. Other options: pinned packages under /usr (the first brief's gap note; a root rollback then silently downgrades the CLI); the vendors' own auto-update.

**Can the machine's own agent edit project code?** Default: no. It reads any project (a project that is "this" is added to its turn while its working folder stays your home, so its session still resumes), answers questions about it, and starts or tells sessions only when asked. A hook on its own CLI denies writes under ~/Projects. Two writers in one folder break both undos. Other options: small edits in the main checkout.

**Does every session get its own ports and database?** Default: yes: ten ports, a COMPOSE_PROJECT_NAME and a BOMBADIL_DB name in its environment, and its own preview browser profile. A project that needs a real database or other services opts into rootless podman in its project file, one stack per session. Semantic clashes, such as two sessions migrating the same schema differently, still surface only at review, and the overlap marks are the early warning. Other options: ports per project (several roles on one repository collide); portless on port 443 (needs sudo and a local certificate authority).

**Are checks run automatically?** Default: only when the project names one, in the background as a job after each turn, with the result on the dot and in the hover peek. A new failure goes back to the session once, marked as coming from the check; the same failure twice makes the dot red and waits for you. This is the confirmed once-per-distinct-error rule applied to projects, and naming the check is the standing instruction. Other options: report only; retry until green (a token furnace).

**What about sessions you start by hand in a plain terminal?** Default: they show as dots marked "yours", with the same your-turn line, but they keep your sudo, run in the folder you started them in, and die with their window as they do today. Typing `claude` and `codex` are never wrapped, because wrappers break `claude -p`, `claude mcp` and `codex exec` in scripts. Other options: wrap the commands; ignore hand-started sessions.

## Everything else, by moment

### Opening a project

- **The project card.** Typing a project's name opens a small card: its sessions as rows, "main: clean" or "3 files changed", open pull requests, and chips for "Claude here", "Codex here" and "Terminal here". Tab on a row brings that session forward. The terminal panel opened from the card starts in the project folder with its tools on the path.
- **Standing roles.** A project file (.bombadil/project.toml) can name roles with a standing brief each (review, batch, docs), the run and check commands, and how work lands (pull request or local merge). "claude bombadil review" then starts with that brief and keeps its name from day to day. The file is optional, and Bombadil never writes into a repository.
- **Names that clash.** Project names, role names, app names and aliases share one list. The order is project, then session, then app, then alias, and when a typed word matches two things Tab shows both as chips ("reviewer (Bombadil)", "reviewer (Acme)").

### Working

- **Peek.** Hovering a dot shows the session's last few plain lines, built locally from its events, never by a model. "what is builder doing?" gets the same two lines from the machine.
- **A second opinion.** "have codex review builder" starts a read-only session from the other vendor on builder's code, as a dot, and its notes come back as its own your-turn line. "send those to builder" pastes them into builder.
- **Watchers for sessions.** "when batch is done, tell reviewer to summarize the failures" becomes a watcher listed under "what are you watching?", with a stop button, and it runs once. It is your instruction, given in advance, not the machine deciding.
- **The other panels.** The browser and Files stay global. A session's preview is its own small window, never a tab in your browser.

### Review and test

- **Since you last looked.** The diff opens at the changes since you last had that session in front, with a toggle for the whole branch, and five seconds of focus counts as looked.
- **A native review app, later.** Once the terminal diff proves too thin, a kit app built from Editor, Highlighter and ItemList shows one entry per turn labeled by the prompt that made it, with notes on lines that go back to the session as "file:line: note". It reads the checkpoint refs from piece 4.
- **Try it for Bombadil itself.** When the project is Bombadil, "run it" boots the VM smoke test as a job, never an install onto the running machine. "install this build" is a machine turn into /usr with a restore point, so dogfooding has one defined path and an undo.

### Away and back

- **Cloud sessions.** Sessions started in Claude Code on the web show in the Claude app already; "bring down <name>" runs the vendor's teleport into a Bombadil copy so it joins the list. Codex's cloud tasks are listed later through its cloud commands.
- **A phone view of Codex.** Until Codex has a Linux phone host, a read-only zellij web token can show a batch's screen on the phone; it is a port, so it is off by default and on by asking.
- **Welcome back.** The first brief's welcome line gains the sessions: what finished, who is waiting, what failed, built locally.

### When it goes wrong

- **Memory.** bombadil-dev.slice has a ceiling, so a runaway build slows the sessions, never the pill. The first brief's memory card gains a sessions group, largest first, and a session idle for twelve hours may be put to sleep and resumed by id when you type its name.
- **A dead session.** If a CLI crashes, the dot turns red with "Why?", which opens its last screen. Resume is one click and never automatic.
- **agentd restarts.** Sessions live in their own units, so editing or restarting agentd (Bombadil is developed on Bombadil) never touches them; signals that arrive while agentd is down are appended to a file and replayed.
- **Provider trouble.** "Claude signed you out" and "Codex is at its limit until 15:00" are one line each, as in the first brief, and the dots of the affected sessions dim.

### Always on

- **The developer note.** One short text, injected only into sessions that carry BOMBADIL_SESSION=1: you have no sudo, use `bombadil ask`; your ports and database name; commit on your branch, Bombadil pushes when the person says ship; report progress with the progress tool. memory.md stays about the person, because every session loads it.
- **Tools per project.** mise provides toolchains, environment and tasks at user level, so sessions rarely need root at all. Headless CLIs never run shell prompt hooks, so sessions start through `mise exec`.
- **Rewind.** Session starts, ends, ships and undos appear as rows in Rewind; a session's inner turns stay in its own terminal.
- **Snapshots.** snap-pac stays on for pacman runs outside a turn. ~/Projects, ~/.local/share/claude, ~/.claude.json and zellij's serialization state join the home-snapshot exclusions.

### Cut, and why

- **A native chat window for coding agents** through the Agent SDK, stream-json or ACP. It would rebuild what the vendors improve every week, lose slash commands, plan mode and subagent switching, sit next to Anthropic's policy on third-party use of the Claude subscription login, and depend on `-p` keeping subscription login once `--bare` becomes its default. The terminal wins, and the kit app for review covers the one place drawing beats scrollback.
- **The vendors' supervisors as the host.** Research preview and experimental, with a one-hour idle stop, self-pushes and a shared environment. Observed, wrapped, never relied on.
- **A workspace per project, tabbed groups, or a grid of agents.** A tmux desktop by another name, and against the confirmed stage.
- **Super+N and Space to peek.** Two new keys. Tab from the empty pill walks the queue, and hovering a dot peeks.
- **The pill rewriting your ask into a brief.** A hidden layer between you and the coder, and one more phrase to learn.
- **A sudo shim that queues the session's exact command to run on its own.** Root work started by an agent, possibly a prompt-injected one, with no tap.
- **Installing packages a session asked for without a click.** The same, one step milder.
- **The machine coordinating sessions on its own, or splitting a goal into workers.** Work nobody asked for; Claude's Projects already does this through Remote Control for those who want it.
- **Reading the vendors' transcript files for status.** Both vendors call the formats internal; hooks and `claude agents --json` are the supported routes.
- **A kanban board.** Dots sorted by state already say what needs you.
- **portless on port 443, a container or a separate user per session, agent teams.** Too heavy, or experimental, for the default.
- **Wrapping the `claude` and `codex` commands in the shell.** Breaks scripts and subcommands.

## Gaps worth thinking about

### In the code today

- Every coding session is a bare foot window started from hl.exec_cmd in hyprland.lua, so it dies with its window and with the compositor.
- Stop kills anything in the turn's process tree outside PROTECTED_ARGS, including a session the agent started for you.
- snapshots.py covers the root config only, and nothing under ~ can be undone; the checkout in ~/Projects has no way back at all.
- agentd's own session always runs from $HOME, so "this" for a project means adding its folder to the turn, not moving the session.

### Not designed yet

- The pill's own words from the phone. "ship", "undo" and "end" are pill words, and the phone reaches only a Claude session, so from the phone you can answer a session but not ship, undo or end one.
- Several monitors, and which screen a session or a project card opens on. Parallel sessions are exactly where a second screen matters. Still open, as in the first brief.
- Headroom for the pill: five coding sessions drain the same Claude plan the machine's own conversation needs. The first brief's "use Codex until 15:00" covers the machine; reserving quota for it does not exist as a vendor feature.
- Real per-session databases for projects that need them: a reflinked SQLite copy or a schema per session, with migrations kept apart.
- Semantic conflicts git cannot see: one session changes an API while another writes tests against the old shape. Overlap marks catch the same file, not the same idea.
- Adopting a person's existing pipelines: sessions already under ~/.claude and ~/.codex, and existing named-session messaging habits, on day one.
- Secrets in copies: .worktreeinclude copies .env files into every copy, and a "clean up" should remove them.
- IDEs as ordinary apps: "install VS Code" or Zed works like any app, and how they land on the stage and follow the theme is the first brief's open question about ordinary Linux apps.

## What would make it feel like an IDE or a tmux mess instead

- Status bars, pane borders, prefix keys or tabs inside the terminal, and bars inside bars: zellij's, then the CLI's, then the pill.
- Six agents tiled in a grid "so you can watch them", or a window opening every time a session starts.
- A file tree, editor, terminal and diff arranged around a sidebar; a permanent sessions sidebar.
- Bombadil slash commands, wrapper prompts or a re-skinned TUI.
- A line in the pill every time any session ends a turn, bells from background panes, or alerts about the session you are looking at.
- Sessions that die when their window closes, worktrees nobody removes, or zellij sessions with no dot.
- Session lists that disagree: the pill, the vendor's own view and the phone must use the same names.
- Coding sessions' transcripts copied into the pill, or tool names, token counts and raw JSON on dots.
- Every server fighting over port 3000, and one test database shared by all sessions.
- A "developer mode" switch that changes the whole OS.

## Extends or changes in the first brief

- **No parallel helper agents; typing during a turn waits:** unchanged for the pill's conversation. Coding sessions are a separate kind of thing: never queued, never given the pill's "this" context, no sudo, no os-mcp system tools. The cut on a Codex second opinion inside the conversation stands; a second opinion is allowed as its own read-only session.
- **One conversation, no threads, no provider picker:** unchanged. Coding conversations live in their own windows, the dots are a job list you cannot chat in, and each session names its own tool.
- **Background jobs as chips:** extended with a your-turn state, one chip per project and a dot per session, folded when there are more than three projects.
- **Notifications are one line in the pill; the agent never starts work on its own:** extended to session attention and to a usage or memory limit near, silent for the session in front, and the phone only while away. Sessions start only when you ask, watchers run once on your words, and nothing resumes after a reboot until you do.
- **No prompts:** narrowed by one tap. Your own asks never prompt; a coding session's request for root waits for Tab.
- **Undo:** unchanged for the bare word. "undo this" and "undo <name>" are added for sessions, live through git. ~/Projects becomes a nested subvolume outside home snapshots, and ~/.local/share/claude, ~/.claude.json and zellij state join the exclusions.
- **Stop:** narrowed. Esc in the pill and Super+Escape stop only the machine's turn; "stop <name>", "end <name>" and "stop everything" are added.
- **Windows keep a close button:** extended. Closing a session's window only closes the viewer; "end <name>" ends the session.
- **A broken app fixes itself, once per distinct error:** extended to project checks. Naming a check in the project file is the standing instruction, and a failure is pasted into the session once, marked "(from Bombadil check)".
- **Code only when asked in words:** narrowed to the machine and its apps. A coding tool you opened by name shows code, and a diff opens only when asked.
- **The stage, no tiling:** unchanged. Session windows are stage windows; a diff or preview uses the second spot beside them.
- **The terminal is a panel:** unchanged for the plain terminal; the project card's "Terminal here" opens it in the project folder.
- **"This":** extended. A session in front makes "this" that session ("builder on Bombadil"), and its last screen lines go into the [Screen] block as untrusted data.
- **What skips the model:** extended with project names, role names, `claude|codex|shell <project> [role]`, "undo this", "undo|stop|end <name>", "stop everything", "what's running?" and "clean up".
- **memory.md as ~/.claude/CLAUDE.md and ~/.codex/AGENTS.md:** kept, and because every coding session now loads it, it must stay about the person; the machine's rules stay in the system prompt, and the developer note reaches sessions only.
- **CLIs packaged in the image with the updater off:** changed to user-level installs seeded from the image, updated by Bombadil at quiet moments.
- **Rule 6, never a web page on a port:** narrowed to what the machine makes. Your own web projects preview on localhost in their own small window.
- **Rewind:** rows for session starts, ends, ships and undos.
- **A rare question as two or three buttons:** not used for sessions yet, because a message from outside cannot answer a vendor TUI's own question; Tab brings the session forward with the question under the cursor.

## Checked today

These are the facts the design rests on, checked against the vendors' current docs on 27 Sep 2026. Builders should re-check the ones marked as moving.

- Claude Code's agent view (`claude agents`, `claude --bg --name`, `claude agents --json --all` as the supported way to read state) is a research preview. A background session moves itself into .claude/worktrees before editing and commits and pushes on its own unless git instructions in the task, CLAUDE.md or memory say otherwise, and the supervisor stops a done or waiting session after about an hour unattached unless pinned. Moving.
- Claude hooks: SessionStart supports command and mcp_tool handlers only, not http. Notification's permission_prompt fires about six seconds late, idle_prompt about a minute late, and agent_needs_input only while agent view is open, so PermissionRequest and PreToolUse on AskUserQuestion are the immediate signals.
- Claude's status line JSON carries rate_limits (five-hour and seven-day) for Pro and Max subscribers after the first response.
- Cross-session messaging: a bypass-mode receiver holds messages unless the sender also bypasses, in which case it delivers them; `-p` receivers drop held messages after five minutes; crossSessionInbound accept or refuse is set per session through --settings.
- Remote Control server mode with --spawn worktree and --capacity is out of research preview, and remoteControlAtStartup applies to interactive sessions only.
- Claude Desktop for Linux is a Debian and Ubuntu beta, and its docs say to run the CLI on Arch. Claude Code itself does not officially list Arch.
- The ChatGPT app for Linux, which carries Codex, officially supports Arch through a signed pacman repository whose installer runs a full `pacman -Syu`; it uses XWayland on Wayland. Codex's `mcp-server` command was removed in 0.154; app-server is experimental; the TUI starts a shared background daemon by default since 0.157, and that daemon updates itself unless turned off. Moving.
- Codex hooks exist (SessionStart, UserPromptSubmit, PermissionRequest, Stop, Interrupt, SessionEnd and more); user-added hooks need a trust review, and hooks in /etc/codex/requirements.toml are trusted by policy. `notify` fires only agent-turn-complete. Codex worktrees default to $CODEX_HOME/worktrees with a configurable root (documented for the app; the TUI's location is not documented).
- foot's [desktop-notifications] command receives OSC 9, 99 and 777 with the app id, title and body, and --toplevel-tag matches Hyprland's xdg_tag rule field. zellij 0.45 has `subscribe` with JSON output and a web client with read-only tokens. Hyprland 0.56 on Arch uses the Lua config and emits bell and urgent events. Quickshell has no terminal widget of its own; qmltermwidget for Qt 6 exists in Arch, untested inside Quickshell.
- systemd user units get memory, pids and cpu delegation out of the box, so slice limits work without root changes. NoNewPrivileges cannot be set on a scope unit, and a service unit whose zellij client forks would need RemainAfterExit, which is why the fence is `setpriv` inside the scope; its no-new-privs flag also breaks rootless podman's newuidmap, hence the socket outside the fence.

**Unverified, and to test in the VM before piece 1 or 3:** zellij passing the kitty keyboard protocol, mouse and OSC notifications (with host_notification_protocol set) through to foot; `setpriv --no-new-privs` side effects beyond sudo and newuidmap (FUSE, browser sandboxes in tests, Codex's bubblewrap); whether managed hooks fire inside the Codex desktop app; that a copy made with `git worktree add` under ~/Projects/.work passes Claude's worktree check; the Codex daemon's socket path; whether a Codex thread loaded by one client can be attached by `codex --remote`.

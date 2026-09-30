# Bombadil chronicle

Bombadil is an AI Linux distro: an Arch-based system where an AI agent (Claude first, Codex too) is the main way to use the machine. The agent opens panels such as a browser, writes native apps on request, and can undo its own changes with a snapshot. Daniel's framing is that the experience must be "stupidly simple to use, yet complete, capable and unique", and since 2026-09-27 11:20 UTC that the OS is for the AI while the user is a passenger who gets things explained visually.

This chronicle covers 2026-09-27 to 2026-09-30. All times are UTC. "The coordinator" is the Claude in the project chat that answers Daniel and starts worker threads (Daniel calls it "the orchestrator"). "Thread" means one worker Claude session with its own branch. The repository is thedefaultman/Bombadil.

## Where things stand

As of 2026-09-30 about 17:55 UTC. The pull requests and branch tips were read from GitHub at 17:50 to 17:55, and the thread transcripts were refreshed at 17:50. Progress changes by the minute: the threads below keep pushing to their branches.

### On main

Main is at d9dde3b. Four pull requests are merged:

- #1 "Bombadil first milestone: agent session, OS tools, native apps, ISO", merged 2026-09-27 09:01. It holds `agentd` (the session daemon that runs the Claude Code or Codex command line tools), `bombadil-os-mcp` (the OS tool server), the QML app runtime, the Quickshell bar, the archiso profile, the installer with snapshot undo, and the container ISO build and VM scripts.
- #3 "Live status line above the pill, and the pill as a launcher", merged 2026-09-27 10:46. It adds the plain-words status line, Stop that ends the whole turn, and opening apps and panels from the pill.
- #6 "Fixes from the laptop VM: the details drawer closes", merged 2026-09-27 12:18.
- #7 "Laptop VM fixes, batch 1: Super tap keyboard, exact launcher smoke, Virtual-1 size", merged 2026-09-30 17:00. A Super tap now gives the pill the keyboard, and the QEMU guest runs at 1920x1080.

Main also holds `.claude/settings.json`, the permissions file Daniel committed himself on 2026-09-27 at 08:31. Before the docs pull request that added this folder, `docs/ARCHITECTURE.md` was the only file in `docs/`.

### Open pull requests, all drafts

- #2 "App kit: a skill and native component kit for building Bombadil apps" (branch `claude/app-kit-x0bitn`). The app skill, OS-styled component kit, charts, native bindings, hot reload, and two example apps (a password manager and a memory viewer). It booted cleanly in a VM and 436 tests passed before main was merged in again at 17:49 (PR #7 had conflicts in `shell.qml` and the smoke script). Left: an ISO rebuild, a boot and the bar test, then merge. The brain thread's built-in-apps patch is folded into it. Three other branches wait for it.
- #4 "Coding sessions that come back by name, and dots beside the pill" (branch `claude/dev-sessions-gqk92e`). Zellij-based sessions with Claude Code and Codex hooks, and per-session dots in the bar. Desktop test 35 of 35 and VM smoke 47 of 47. It waits for #2, then main is merged in once and the tests rerun.
- #5 "Sign in to Claude or Codex from the pill, with the page in the browser panel" (branch `claude/native-login-zfasd2`). VM smoke 63 of 63, 297 tests. A second code review is being rerun, then merge.
- #8 "Laptop VM fixes, batch 2: status line, audio, mirrors, app spots, GRUB, connectors" (branch `claude/vm-fixes`), opened at 17:17 with batch 2 items 1 to 7. Item 8, Alt+Space as a second binding for the pill, was pushed at 17:29. CI is green and the install-mode VM smoke on the built ISO was running at 17:50, then merge. Batch 3 (snapshot pruning, a hidden browser crash-restore bubble, "stopped" instead of "failed (exit -2)", and more) is partly committed on the same branch and waits for #8 to merge.
- #9 "The desk: rails, Now, strips, and Watching and Needs you" (branch `claude/desk-yp3ltq`), opened at 17:29. Rails, Now, the chips that fold, and the Watching and Needs you rows are in (desktop test 22 of 22 earlier, tests for the new rows added at 17:38). The jobs registry in `agentd` and two independent reviews were still running at 17:52, then a real Hyprland check and merge.
- #10 "Why lines and pictures from the machine" (branch `claude/passenger-g7vhu1`), opened at 17:52. Pieces 1 and 2 of the passenger brief: the "why" and "after reading" lines on the status line's hover, and pictures of the machine drawn above the pill (`show_card`, `system_map`, before and after receipts). 704 tests pass and the desktop test passes 29 of 29. It carries the app kit's files and three plan-tool commits from the desk branch until #2 lands, so its diff shrinks then. It merges after #2.

### Branches with no pull request yet

- `claude/brain-8d4ltb`: the brain's self-writing index, root file watcher and Focus view. 614 tests passed with main merged in at 17:04. At 17:49 the watcher was changed to name a writer once per run of events (a 20,000-file write had overflowed the client buffer) and rows that are not turns stopped counting as turns. At 17:55 its docs were extended so the watcher's edge reads as a contract a replacement must keep. Next are an ISO build from the branch plus the app kit, a VM smoke with brain checks, then a PR.
- `claude/windows-vm`: the scripts that run the VM in a window on Windows through WSL (`scripts/bombadil-vm.cmd`, `scripts/wsl-vm.sh`). It is now on origin.
- `claude/first-milestone`: kept on purpose after #1 merged, because the dependent PRs were based on it.
- `claude/blissful-ramanujan-5o1p2h`: three commits from Claude sessions that none of the thread transcripts cover. They add a remote-use design (`docs/REMOTE.md`, 2026-09-27 20:36) and a skill for writing design briefs as pages (`.claude/skills/bombadil-brief`, 2026-09-28 16:10 and 18:42). No PR. Check with Daniel before relying on it.

### Threads still working

"App skill and native component kit" (ISO rebuild, then PR #2), "Named coding sessions and turn dots" (waiting on #2), "Native provider login" (second code review on #5), "Brain index and Focus view" (finishing the branch before its PR), "Laptop VM findings fixes" (PR #8 install smoke, then batch 3), "Bombadil VM on the laptop" (a fresh session on Daniel's laptop moved the VM to main d9dde3b in place and is testing the keyboard hand-back, then batch 3), "Desk rails and first widgets" (PR #9), "Why lines and machine pictures" (PR #10, waiting on #2), "Setup voice and welcome lines" (plan accepted, building pieces 1 and 2 in the thread itself), "Noticed chip and self-checks" (builds the first two pieces of the self-improvement loop; in phase 1 at 17:47 with no branch pushed yet), and "Portable project bundle" (packing this folder and the docs pull request). Two design threads are also working and have delivered nothing yet: "Bombadil logo and design system" (drawing logo directions) and "Bombadil as an installed OS" (the design of the whole install flow).

Finished: "Foundation choices for the distro", "Build and boot the first ISO", "User experience ideas", "Live status line and pill launcher", "Development experience ideas", "Files, context and brain ideas", "AI native OS missing pieces", "Desktop widget designs", "Self-improvement loop" (Daniel accepted it at 17:32), and "Rust or not for Bombadil" (answered, decided at 17:45).

### Owed to Daniel (he is waiting on Claude)

- The answer to his question of 2026-09-30 at 17:07: what installing Bombadil to disk, as an operating system and not only from a flash drive, looks like, and whether it changes the current work. "Bombadil as an installed OS" is still working on it.
- Logo options and how they look in different states (asked at 17:12). "Bombadil logo and design system" is still drawing them.

### Waiting on Daniel

- Nothing gates any merge on him.
- One optional word on the Rust question: the plan in force is "a" (keep Python and QML, fix the three problems now). Saying "b" would also start a Rust rewrite of the brain's file watcher right after the brain merges.

### Known gaps with no owner

These came from the first-milestone review and are not claimed by any thread: the turn counter resets when `agentd` restarts, the session id is kept in memory only, the EFI partition is at /boot, snapshots are never cleaned up (batch 3 of the laptop findings starts on it), Chromium has no `--user-data-dir`, the bar has no input mask, the CLIs are installed through npm under /usr, and multi-monitor and keybinds are not handled. Two more findings from the Rust survey are routed to "Laptop VM findings fixes" for after #8: `agentd` and Quickshell do not restart after a crash, and one unexpected line from the CLI ends a turn and drops its answer. A real Codex conversation has not been tried (Claude sign-in worked on Daniel's VM on 2026-09-27 at 11:37). Batch 1 findings 3 and 4 (a finished status line covers a new app, and apps stack in one spot) went to the app kit thread, and PR #8 also changes app placement, so check the overlap when #2 lands.

## Timeline

The project threads were paused on 2026-09-27 from about 12:24, resumed on 2026-09-28 at 05:10, and paused again from about 05:26. They resumed for a few hours early on 2026-09-29 (about 00:02 to 01:15) and then again on 2026-09-30 at 17:00. A Claude session outside the project threads committed to one branch during the pauses (see 2026-09-27 20:36 and 2026-09-28 16:10).

### 2026-09-27

- 04:50 The coordinator opened the project and asked three starting questions: base distro, compositor or bare framebuffer, and VM or real hardware first.
- 04:51 Daniel asked for recommendations with pros and cons. The thread "Foundation choices for the distro" started.
- 04:53 Claude delivered `foundation-choices.md` with seven recommended picks and built nothing yet.
- 04:55 Daniel accepted all seven picks by replying "all". Claude asked for an empty repo.
- 04:58 Daniel created the repo thedefaultman/Bombadil (Claude had suggested the name ai-distro) and added it to the project. Claude cloned it and began the first milestone.
- 05:00 Daniel installed the Claude GitHub App on the repo, and reconnected GitHub at 05:11 after pushes were still refused.
- 05:08 Claude committed the first milestone on `claude/first-milestone`.
- 05:11 Claude pushed it and opened draft PR #1.
- 05:15 Daniel chose "container" over "device" for the first real ISO boot, so the ISO is built in Docker and booted headless in QEMU.
- 05:19 Daniel gave the environment full network access so Arch mirrors could be reached.
- 05:22 The thread "Build and boot the first ISO" started on the new environment and took over PR #1.
- 05:55 Daniel asked whether threads could run with full autonomy, since he was approving permission prompts by hand. The coordinator answered at 06:07 that cloud threads have no bypass mode and that a committed `.claude/settings.json` is the way.
- 06:22 Daniel told the ISO thread "add the permissions file". A safety check still blocked the thread from committing it (reported at 06:28), so it handed Daniel the file to commit.
- 07:08 Daniel asked whether the distro needs a skill or framework for making native visual interactives for the user, in the fastest way possible. The coordinator said yes, and that it is the piece that makes Bombadil feel like an OS.
- 07:10 Daniel said "go". The thread "App skill and native component kit" started.
- 07:14 Daniel asked for ideas on the user experience. The thread "User experience ideas" started.
- 07:27 The review of the first milestone against the upstream APIs, by Claude in "Foundation choices for the distro", was written up as a 34-item findings file, 13 of them blocking (for example, "undo that" picked the wrong snapshot). The coordinator handed the file to the ISO thread.
- 07:28 Daniel asked whether the foundation thread was done, and the coordinator marked it finished at 07:29.
- 07:35 "Build and boot the first ISO" folded the confirmed review fixes into PR #1.
- 08:09 The ISO thread reported the ISO boots and the milestone works in a headless VM: bar, browser panel, a generated native app, and install plus undo across a reboot.
- 08:12 Claude delivered "How Bombadil Feels" and `ux-brief.md`: 44 ideas and 29 decisions with defaults.
- 08:18 Daniel asked for an update. The coordinator reported at 08:19 that the ISO thread was green and waiting only on the permissions file.
- 08:27 Daniel said he agrees with the decisions in the UX thread and asked for the permissions file to be committed. The thread "Live status line and pill launcher" started on the first two UX pieces. All 29 UX defaults were marked confirmed.
- 08:29 "Build and boot the first ISO" committed the permissions file on `claude/first-milestone` (6c3450b), and at 08:30 "Live status line and pill launcher" committed its own copy on its branch (678c0fe).
- 08:31 Daniel committed `.claude/settings.json` directly on main. Threads read that file from the default branch when they start, so the copies on side branches did not count for new threads until then.
- 08:37 Daniel asked whether he needed to do anything for PR #1. At 08:39 he said: "You can merge the PRs as they are needed. They don't have to be gated on me."
- 08:39 Draft PR #2 (app kit) was opened.
- 08:47 Daniel asked for a second user experience pass on running Claude Code and Codex in the distro and working on his other projects dynamically. The thread "Development experience ideas" started.
- 09:01 PR #1 merged after the review-fix build passed all VM checks (live, install, installed disk, and undo after reboot).
- 09:11 Daniel told the app kit thread "push" after a permission check blocked a push, and asked for a secret-scan false positive to be dismissed. He marked it a false positive at 09:16.
- 09:19 Daniel asked why the app kit thread looked blocked. The coordinator explained it was waiting on a slow VM boot.
- 09:49 Draft PR #3 (status line and launcher) was opened, after a headless desktop run with the real bar and agent passed 18 of 18 checks.
- 10:10 Daniel asked for a VM on his laptop that he and Claude can both run, so he experiences it and Claude sees what works and what fails. The thread "Bombadil VM on the laptop" started.
- 10:18 Daniel approved the plan to build the VM in an Arch WSL distro with QEMU and archiso, using KVM inside WSL.
- 10:34 Claude delivered "Building on Bombadil" and `dev-brief.md`: six pieces and 19 defaults.
- 10:46 PR #3 merged.
- 10:46 Daniel accepted the development brief and its defaults. The thread "Named coding sessions and turn dots" started on its first two pieces.
- 10:46 Daniel also asked for a native file, context and "brain" for Bombadil that looks like Obsidian, stays current, is easy to read, and does not become another stale markdown pile. The thread "Files, context and brain ideas" started at 10:47.
- 10:48 Daniel clarified that by "Obsidian view" he meant the graph view, and asked to be shown it next to any better alternative. He also asked for an update on what was done.
- 11:07 Daniel reported that `/login` in the terminal only prints a URL and asked for native sign-in, through Bombadil's browser if need be. The thread "Native provider login" started.
- 11:08 Claude delivered "Bombadil's Brain" and `brain-brief.md`: Focus, Map and Time over a self-writing index, with 17 defaults. It recommended against the graph as the main view.
- 11:12 Daniel said he loved the recommendations and "lets do this". The thread "Brain index and Focus view" started.
- 11:20 Daniel asked what else an AI native OS needs, imagined as something for the AI with the user as a passenger who gets things explained visually. The thread "AI native OS missing pieces" started.
- 11:22 Draft PR #4 (coding sessions and dots) was opened.
- 11:23 Daniel asked for a few desktop widget designs that are modular and can move, hide and make room for apps, popups and other visuals the AI shows. The thread "Desktop widget designs" started.
- 11:29 Claude handed Daniel the VM, running main with the status line and launcher, at 1600x900.
- 11:37 Daniel logged in to Claude inside Bombadil and it worked. His first real conversation produced a native Memory Monitor app in about a minute.
- 11:38 Daniel said he could not close the details drawer and asked Claude to run a batch of tests in his VM and send every finding to the orchestrator. The thread "Laptop VM findings fixes" started.
- 11:49 Draft PR #5 (native sign-in from the pill) was opened.
- 12:01 PR #6 was opened, and it merged at 12:18 after 37 of 37 VM smoke checks on real Hyprland.
- 12:21 Claude delivered "The Bombadil Desk" and `widgets-brief.md`: 18 mock-ups and 15 defaults. The coordinator relayed Daniel's laptop finding that the agent ran `find /` to locate `Theme.qml`, so the app kit thread was told to name the kit's location in the skill and in every turn's instructions.
- 12:24 Threads were paused.
- 20:27 Daniel asked for a one-time setup widget that gives Bombadil a voice (what to call the user and a few simple options, "don't make it too complicated"), plus a format for welcome and empty-state lines such as "Welcome [Daniel], Let's go for a walk!". The thread "Setup voice and welcome lines" started.
- 20:36 A Claude session outside the project threads committed a remote-use design (`docs/REMOTE.md`) on `claude/blissful-ramanujan-5o1p2h`.

### 2026-09-28

- 05:10 Daniel asked for the work to continue and for everything, including the pages, to be saved into a local folder he can carry elsewhere. The thread "Portable project bundle" started at 05:13.
- 05:14 Daniel asked how to turn Remote Control back on for the laptop folder. The coordinator gave the command to run there.
- 05:16 Daniel accepted the Desk design and its 15 defaults ("Lets start on that"). The thread "Desk rails and first widgets" started.
- 05:18 Daniel asked for a self-improvement loop: count his repeated requests, show them in a widget, offer to turn them into a widget or app, and let Bombadil find, report and fix its own issues and grow its component libraries. It must not feel like something he maintains, but he should see what it improved. The thread "Self-improvement loop" started.
- 05:22 Daniel asked whether he could open the project and its threads in the Claude Code terminal. The coordinator answered at 05:26 that threads can be pulled in with `claude --teleport`, but the project view cannot.
- 05:26 Threads were paused again.
- 16:10 The same branch gained the `bombadil-brief` skill (design briefs as a page with drawn mock-ups). No transcript shows who asked for it.
- 16:50 Daniel renamed the project Bombadil.
- 18:42 The skill's series list was updated with "The Bombadil Kit" and "Bombadil at Work".

### 2026-09-29

- 00:02 Daniel asked for the stopped threads to continue. At 00:22 he asked again.
- 00:04 The bundle thread asked how much should go into the public repo. Daniel chose "Briefs and pages" at 00:12.
- 00:11 Daniel asked which Remote Control mode to pick on his laptop. The coordinator said same-dir. A fresh laptop session took over at 00:17.
- 00:16 Claude delivered "Riding with Bombadil" and `passenger-brief.md`: six pieces and 20 defaults, with 13 mock-ups. Trust, other-machines and OS-basics items were marked "not checked".
- 00:28 Daniel said "go" on the passenger brief. The thread "Why lines and machine pictures" started at 00:29 on its first two pieces.
- 00:34 Daniel asked whether Bombadil should use Rust for some of the Linux work, which languages the project uses, and for Rust to be explained. The thread "Rust or not for Bombadil" started.
- 00:49 The laptop thread tested undo, Stop and offline recovery on Daniel's VM and found seven new problems, among them no sound, no package mirrors and a stuck status line.
- 01:00 PR #7 was opened by "Laptop VM findings fixes".
- 01:11 The laptop thread moved Daniel's VM to main (089f181) in place, keeping his login and apps.
- 01:15 Threads were paused.

### 2026-09-30

- 17:00 Daniel asked for the stopped threads to continue. PR #7 merged in the same minute.
- 17:02 The coordinator relayed batch 2 of the laptop findings (eight items) to "Laptop VM findings fixes". It also posted a decision card about connectors.
- 17:04 Daniel chose "Bombadil only" on that card, so the agent loads only Bombadil's own tools and stray "authorize Gmail" notices stop. He also asked the coordinator to make sure the laptop thread continues.
- 17:06 Daniel told the laptop thread "continue" once his laptop was connected, and a fresh machine session took over.
- 17:07 Daniel said Bombadil will be used not only from a flash drive but installed as an operating system, the way one installs Debian or Windows, and asked what that looks like and whether it changes the current work. The thread "Bombadil as an installed OS" started.
- 17:09 "Setup voice and welcome lines" merged its drafts and wrote `voice-brief.md`.
- 17:10 Daniel said the old laptop session had stopped answering. A fresh session started in the same thread with a catch-up brief and moved his VM onto the Super key fix.
- 17:12 Daniel asked for a Bombadil logo, a design identity and a design system, with a few logo options shown in different states. He likes the current look. The coordinator gave it to a new thread, "Bombadil logo and design system", so the voice thread keeps the spoken voice, name and welcome lines and leaves visual marks to that thread.
- 17:15 "Self-improvement loop" finished writing its brief (`self-improvement-brief.md`). Its mock-up page was published by 17:24.
- 17:17 Draft PR #8 was opened with batch 2 items 1 to 7.
- 17:21 Daniel asked for an update on the two threads that looked ready for review. The coordinator answered at 17:22 that neither needed a review from him: both merge themselves once their checks pass.
- 17:21 The voice thread posted its options: a setup card with three voices, one fading line above the pill for welcomes, and the voice reaching greetings, goodbyes and the last sentence of a reply.
- 17:22 The coordinator told the design thread "Bombadil as an installed OS" that the installer's time zone step is its to design. That thread owns the whole install flow, which PR #8 also edits.
- 17:24 Daniel accepted the voice plan ("Go on the build"). The voice thread is building pieces 1 and 2 itself, with the recommended picks, so no separate build thread starts.
- 17:24 The self-improvement thread posted three lettered choices.
- 17:25 Daniel asked what the Windows-key question on item 8 meant. The coordinator explained at 17:26 that Windows often keeps that key for its Start menu inside a VM window, and that Alt+Space would become a second way to open the pill.
- 17:28 Daniel answered "Start menu". At 17:29 the coordinator told "Laptop VM findings fixes", which pushed Alt+Space to PR #8.
- 17:29 Draft PR #9 (the desk) was opened.
- 17:29 Daniel asked for the self-improvement choices to be explained in plain words, and the coordinator answered from the brief. The self-improvement thread was told to write in full plain sentences from then on.
- 17:32 Daniel accepted the self-improvement design with 1a, 2a and 3a and said to approve the plan. The build thread "Noticed chip and self-checks" started by 17:34, and the design thread recorded the picks in its brief.
- 17:39 "Rust or not for Bombadil" delivered its answer with measurements (see `rust-question.md`). Its findings went to the brain thread (the watcher's repeated block overflows a buffer) and to the laptop fixes thread (no restart after a crash, and an odd CLI line ends a turn).
- 17:42 Daniel asked for the Rust findings and recommendations. The coordinator summarised them, with three picks: keep Python and QML and fix the problems now, the same and then a Rust watcher, or Rust now for small helpers.
- 17:44 Daniel said keeping it the same and fixing the problems sounds good, and asked whether the Rust watcher should be done now to get ahead of a rewrite. At 17:45 the coordinator recommended fixing now and moving the watcher to Rust only if a measurement says so, since the brain has not merged and three threads wait on it. Saying "b" would start the Rust watcher right after the brain merges. The brain thread was told to keep the watcher's edge thin.
- 17:52 Draft PR #10 (why lines and pictures from the machine) was opened.

## Decisions

The briefs named below are in `docs/design/`.

- Base, display, first target, agent runtime, apps, provider switching and browser: Arch with btrfs snapshots as the undo button, Hyprland first (custom compositor later), VM and ISO first, the official Claude Code and Codex command line tools inside a session daemon, QML native apps, one MCP server for OS abilities, Chromium. See `docs/design/foundation-choices.md` and `docs/ARCHITECTURE.md`.
- The repo is named Bombadil. The first ISO boot test runs in a container with headless QEMU.
- The everyday experience: at rest the screen is wallpaper and one pill. The user learns only Super to talk, Esc to stop and "undo". Apps you make stay until you delete them. AI text is one line while working and at most four lines when done. All 29 UX defaults are confirmed. See `docs/design/ux-brief.md`.
- Coding sessions run in parallel but fenced (as the user, no sudo, their own copy, own ports and undo), outlive their window and return by name. "stop <name>" and "end <name>" are the one exception to the plain undo and Esc rules. All 19 defaults are confirmed. See `docs/design/dev-brief.md`.
- The brain is a self-writing index shown as Focus (default), Map (overview) and Time (a strip under both). The graph is not the main view. Build order is the index, Focus, search from the pill, then brain tools for the agent, then Map and Time. All 17 defaults are confirmed. See `docs/design/brain-brief.md`.
- The passenger framing: six pieces (why and after-reading-what lines, pictures from the machine, a browser with an orange frame and takeover, promises that survive a restart, readable memory, watchable updates). Nightly installing of updates was cut, while downloading stays. The 20 defaults were accepted with "go". See `docs/design/passenger-brief.md`.
- The Desk: modular widgets that yield and reserve no space, present only when they have something to say, with three faces each (rail card, chip beside the pill, mark on the dot). The agent rearranges the desk only when asked. All 15 defaults are confirmed. See `docs/design/widgets-brief.md`.
- Merges are not gated on Daniel. Threads merge their own PRs once verification is green, with a merge commit and never a force-push.
- A committed `.claude/settings.json` on the default branch pre-approves the commands threads run. Threads read it from main when they start, so a rule added on a side branch only helps once it reaches main. Daniel put the first version on main himself at 08:31.
- For now, sign-in happens through Chromium in the VM. Native sign-in through the browser panel is PR #5.
- Daniel's laptop VM is never reinstalled or wiped. New builds are applied in place so his Claude login survives.
- The agent loads only Bombadil's own tools (`--strict-mcp-config`), chosen by Daniel on 2026-09-30.
- The Desk build owns the plan-tool switches in `agentd` (`CLAUDE_CODE_ENABLE_TODO_TOOLS=1` for Claude Code, `-c tools.update_plan.enabled=true` for Codex) so that Now has something to show.
- The public repo gets the briefs, pages, review notes, screenshots and this chronicle, under `docs/` (Daniel's choice of 2026-09-29 at 00:12, "Briefs and pages"). Chat transcripts and project memory stay in a private bundle because they name Daniel's machine and his conversation.
- Voice and welcome lines belong to the voice thread, and logo, identity and design system belong to the "Bombadil logo and design system" thread.
- The installer's time zone step belongs to the "Bombadil as an installed OS" design thread. The voice build shows a plain "Welcome" until a time zone is set, reads the zone once one exists, and does not touch `bombadil-install`.
- The voice (accepted 2026-09-30 at 17:24, with the recommended picks): right after sign-in one small card asks what to call the user and offers three voices, Merry, Plain and Quiet. The welcome is one line above the pill that fades. The voice reaches greetings, goodbyes and the last sentence of a reply, not the working line. See `voice-brief.md`.
- The self-improvement loop (accepted 2026-09-30 at 17:32 as 1a, 2a and 3a): a repeated request may become anything that fits (a word, widget, app, routine, reminder or remembered preference), with one recommended and a tap to choose. The noticed chip shows only when something needs his answer, a line in the welcome-back card says what changed (each with Undo), and before and after pictures come before any change he would notice. Bombadil may fix its own bugs only while he is away, confirmed once with a Start tap, each fix with Undo, and his 2026-09-28 message counts as the permission. Counting requests and the first offers are built first, then the self-checks. Fixing comes later, once the locked-down workspace it needs exists. See `self-improvement-brief.md`.
- Rust (answered 2026-09-30 at 17:39, plan "a" in force after 17:45): Bombadil keeps Python and QML now, because it mostly waits on the model and generated apps must stay QML. Rust fits later in the custom compositor and, only if a measurement says so, in the brain's file watcher. The three problems the survey found (no restart after a crash, one odd CLI line ends a turn, the watcher's repeated block) are fixed in Python first. See `rust-question.md`.
- The Windows key opens the Start menu inside Daniel's VM window (his answer of 17:28), so Alt+Space opens the pill as a second binding (PR #8).

## Threads

| Title | Started | What it did | Outcome |
|---|---|---|---|
| Foundation choices for the distro | 09-27 04:51 | Recommended seven picks, wrote the first milestone, opened PR #1, reviewed the code (34 findings). | Finished. Picks accepted. PR #1 handed to the ISO thread. |
| Build and boot the first ISO | 09-27 05:22 | Built the ISO in a container, fixed the boot, renderer and installer problems, folded in the review fixes. | PR #1 merged 09-27 09:01. |
| App skill and native component kit | 09-27 07:10 | Built the app skill, component kit, bindings, hot reload and two example apps. | PR #2 open, draft. Waits on main merge and a last ISO boot. |
| User experience ideas | 09-27 07:14 | Wrote "How Bombadil Feels" and the UX brief. | Finished. 29 defaults confirmed. |
| Live status line and pill launcher | 09-27 08:27 | Built the status line, launcher, Stop and queue chips. | PR #3 merged 09-27 10:46. |
| Development experience ideas | 09-27 08:47 | Wrote "Building on Bombadil" and the dev brief. | Finished. 19 defaults confirmed. |
| Bombadil VM on the laptop | 09-27 10:10 | Built the WSL and QEMU VM on Daniel's laptop, ran tests, sent findings in batches. | Running. The VM is on main d9dde3b (17:24). Testing the keyboard hand-back, then batch 3. |
| Named coding sessions and turn dots | 09-27 10:46 | Built zellij-based sessions, hooks and dots. | PR #4 open, draft. Waits on #2. |
| Files, context and brain ideas | 09-27 10:47 | Designed Focus, Map and Time, with mock-ups. | Finished. 17 defaults confirmed. |
| Native provider login | 09-27 11:07 | Built sign-in from the pill through the browser panel. | PR #5 open, draft. Second code review. |
| Brain index and Focus view | 09-27 11:12 | Built the index, root watcher, witnesses, Focus and the agent hooks. | Branch pushed, no PR yet. Fixing the watcher overflow and the turn counter at 17:49, then ISO build, VM smoke and PR. |
| AI native OS missing pieces | 09-27 11:20 | Gap analysis for the passenger framing. | Finished. Brief delivered 09-29 00:16. "go" at 00:28. |
| Desktop widget designs | 09-27 11:23 | Designed "The Bombadil Desk" with 18 mock-ups. | Finished. Accepted 09-28 05:16. |
| Laptop VM findings fixes | 09-27 11:38 | Fixes what the laptop VM finds, one commit per finding. | PR #6 and #7 merged. PR #8 open, draft, CI green, install-mode VM smoke running at 17:50. Batch 3 partly committed. |
| Setup voice and welcome lines | 09-27 20:27 | Designing the one-time voice setup and welcome lines. | Brief and page written. Plan accepted 09-30 17:24, building pieces 1 and 2 in this thread. |
| Portable project bundle | 09-28 05:13 | Packed briefs, pages, review notes, screenshots and this chronicle into `docs/` (the docs pull request that added this folder), and the chat transcripts, memory and a git bundle into a private folder with a zip. | Delivered with this pull request. |
| Desk rails and first widgets | 09-28 05:16 | Builds the Desk rails, folding, Now, then Needs you and Watching. | PR #9 open, draft. The jobs registry and two reviews were running at 17:52. |
| Self-improvement loop | 09-28 05:18 | Designed the request-counting and self-fixing loop. | Finished. Brief and page written, Daniel accepted 1a, 2a and 3a at 17:32. The build runs in "Noticed chip and self-checks". |
| Why lines and machine pictures | 09-29 00:29 | Built "why" lines and pictures from the machine (pieces 1 and 2 of the passenger brief). | PR #10 open, draft, 704 tests. Merges after #2. |
| Rust or not for Bombadil | 09-29 00:34 | Surveyed which language each part uses, measured Python against Rust. | Finished 09-30 17:39. Not yet; keep Python and QML and fix three design problems now. |
| Bombadil logo and design system | 09-30 about 17:13 | Designs the logo, design identity and design system. | Running. Nothing delivered yet. |
| Bombadil as an installed OS | 09-30 by 17:07 | Designs the whole install flow, including the installer's time zone step. | Running. Nothing delivered yet. |
| Noticed chip and self-checks | 09-30 by 17:34 | Builds pieces 1 and 2 of the self-improvement loop: counting requests with the first offers, then the self-checks. | Running. Phase 1 at 17:47, no PR yet. |

## How Daniel works with Claude here

- Merges are not gated on him. He said: "You can merge the PRs as they are needed. They don't have to be gated on me." Threads merge their own PRs once their tests and VM checks pass, then tell him in a line or two. Only irreversible steps or changes to what he asked for wait for his words.
- He wants threads to have full autonomy and no permission prompts. Cloud threads have no bypass mode, so the answer is a committed `.claude/settings.json` on the default branch (web fetches plus narrow command prefixes; a blanket Bash rule is ignored). Threads read it from main, so a rule a thread adds on its own branch only helps once it reaches main. Daniel put the first version on main himself at 08:31. Threads can commit rule changes (two did on 2026-09-27), but they take effect for new threads only after they land on main. A thread that still hits a prompt adds its rule to that file.
- He answers lettered or numbered options with one word: "all", "container", "go", "push", "continue". Keep asks that short, and put the recommended option first.
- Design passes follow one shape. A short reply leads with the few things to build first. Defaults are chosen and argued. The full brief is a file under `docs/design/`, with a published page of mock-ups whose HTML sits in `docs/design/pages/`. Daniel usually accepts a pass wholesale ("I agree", "I love the recommendations", "go"), and the first two pieces are then built without another yes. A design thread cannot start threads, so the coordinator starts the build thread.
- Related asks often arrive minutes apart ("Also make...", "I also want..."). Each gets its own thread, and the earlier threads are told what the new one owns.
- The VM testing loop: Daniel runs Bombadil in a VM on his laptop. The laptop thread tests and sends numbered batches of findings to the coordinator, the coordinator hands them to the fix thread (one commit per finding), and once a fix merges the laptop thread moves his VM to the new main in place. While that thread drives the VM window, Daniel leaves it alone.
- How a change is proven: unit tests, the headless desktop test (`tests/desktop/run.sh`), then a container ISO build (`scripts/build-in-container.sh`) checked headless in QEMU with the VM smoke checks (`scripts/test-vm.sh`, and `MODE=install` for install and undo across a reboot). `scripts/run-vm.sh` is only the interactive boot. Anything needing real Hyprland is called out as untested until a VM run covers it.
- The repo's secret scanner checks every commit of a PR, so a fake credential in a test fixture turns it red even after a later commit removes it. Build such fixtures at run time.
- If you pick this up with a fresh Claude: read the briefs in `docs/design/` first, then the open PRs above, and keep the "never wipe Daniel's VM" rule.

## Where to find more

- `docs/README.md` lists everything in this folder.
- `docs/ARCHITECTURE.md`: the foundation decisions, how one turn works, how generated apps work. On branches not yet merged, `docs/BRAIN.md` (the brain) and `docs/REMOTE.md` (the remote-use design) add to it.
- `docs/design/`: the nine briefs. `foundation-choices.md`, `ux-brief.md`, `dev-brief.md`, `brain-brief.md`, `widgets-brief.md`, `passenger-brief.md`, `voice-brief.md` and `self-improvement-brief.md` hold the decisions Daniel confirmed, each with its defaults. `rust-question.md` is the survey behind the Rust answer.
- `docs/design/pages/`: the published pages as HTML, namely `how-bombadil-feels.html`, `building-on-bombadil.html`, `bombadils-brain.html`, `the-bombadil-desk.html`, `riding-with-bombadil.html`, `bombadils-voice.html` and `bombadil-tends-itself.html`. They are copies of what the live pages showed on 2026-09-30. The logo and installed-OS threads had not published a brief yet.
- `docs/review/`: review notes, including the 34-item review of the first milestone. `docs/screens/`: screenshots from headless VM runs (the bar, the browser panel, a generated native app, the pill walkthrough).
- `scripts/build-in-container.sh` builds the ISO. `scripts/test-vm.sh` boots it headless and runs the smoke checks (`MODE=install` adds install and undo across a reboot). `scripts/run-vm.sh` is the interactive boot. `scripts/bombadil-vm.cmd` runs it on Windows and lives on the `claude/windows-vm` branch. `tests/desktop/run.sh` is the headless desktop test.
- The full chat transcripts (the project chat and every thread) and the project memory are not in this public repository. They are kept in a private bundle outside it.

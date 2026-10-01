# Bombadil tends itself: counting what you ask, and finding, fixing and reporting its own bugs

> **Status:** In progress. Pieces 1 and 2 are on an unmerged branch (see "Where the pieces stand"); pieces 3 to 6 are designed.
> **Code:** nothing on `main`. The unmerged work adds `src/bombadil/loop/`, `share/apps/noticed/`, `shell/LoopState.qml`, `shell/NoticedChip.qml`, `shell/NoticedCard.qml` and `bin/bombadil-probe`
> **Design:** this brief; the drawn mock-ups are on [Bombadil tends itself](pages/bombadil-tends-itself.html)
> **Decided by:** the owner, 30 Sep 2026 (options 1a, 2a and 3a chosen, plan approved)
> **Verified:** 2026-10-01 against `main` at `26843d3` (no loop code is on `main`; the unmerged work was read on its own branch)

This design pass runs beside the one on Bombadil's voice ([voice-brief.md](voice-brief.md)). The owner asked on 28 Sep 2026 for a self-improvement loop: Bombadil tracks the requests the person repeats, counts them, shows them a widget about them and offers to turn one into a widget, an app or whatever fits; and it dogfoods itself, finding its own issues, reporting them and fixing them, growing its component library, making new processes and apps, without ever feeling like something they have to maintain, while still letting them see what it improved. It sits on the confirmed briefs ([How Bombadil should feel](ux-brief.md), [Building on Bombadil](dev-brief.md), [Bombadil's Brain](brain-brief.md), [The Bombadil Desk](widgets-brief.md)) and on [Riding with Bombadil](passenger-brief.md), which the owner accepted on 29 Sep. Where a piece here needs one of their decisions extended, the change is named and argued at the end. Nothing here was built when this was written (30 Sep 2026); the status block above says where each piece stands.

**Accepted by the owner on 30 Sep 2026:** options 1a, 2a and 3a were chosen in the decisions below and the plan was approved, so every default here is settled. Pieces 1 and 2 are built first.

The request's mention of creating bugs is read as "file bug reports": the loop writes up what it cannot fix and sends it to the project when the person says so.

The page with the drawn mock-ups is [pages/bombadil-tends-itself.html](pages/bombadil-tends-itself.html).

Refs: `main` = the `main` branch at commit d9dde3b (30 Sep 2026, after the Super tap fix). `kit`, `dev`, `brain` and `login` are the unmerged work for the app kit, the coding sessions, the brain and native sign-in, as it stood then. A citation such as `main:src/bombadil/agentd.py:540-545` is a file and line at that commit (read it with `git show d9dde3b:src/bombadil/agentd.py`), and "today" in this brief means 30 Sep 2026 at that commit. UB, DB, BB, WB and RB are the [ux](ux-brief.md), [dev](dev-brief.md), [brain](brain-brief.md), [widgets](widgets-brief.md) and [riding](passenger-brief.md) briefs; a citation such as UB:140 is a line of that brief as it was written then, so find it by the words quoted beside it. "Inferred" marks what I did not check myself.

## What it looks like

**Tuesday 09:05.** The person opens the laptop. Wallpaper, the pill, and the desk's Away card, which was going to rise anyway after a night away: "batch finished, 300 done", "reviewer is waiting [Open]", and one orange row, "Changed 2 things about itself [See]", with two muted lines under it: "New apps no longer open on top of each other." "The agent restarts in 1 s instead of 4." They tap Open on reviewer and the card goes. Right of the pill a small chip reads "noticed 1". They ignore it all morning.

**Tuesday 13:10.** The person hovers the chip. A card rises from the right rail: "show me my passwords · 4 times on 3 days", "Say "my passwords" and Passwords opens. No model, under a tenth of a second." [Make the word]. They tap it. The line above the pill says "Made "my passwords" open Passwords." with Undo. The chip is gone.

**Thursday.** The chip is back: "noticed 1", a change they would notice. The card shows two small pictures, "Before: the line sits on the app" and "After: the app opens clear of it", and "Checked 4 ways, reviewed by Codex." [Use it] [Not now]. They tap Use it; the bar blinks once.

In the whole week the person saw one Away row, one chip twice, and tapped twice. They learned no new word: "noticed" is the widget's name, and "show noticed" or clicking the chip opens its card. The one time they want to read everything, the card's last line opens a window with what they ask most, what Bombadil changed about itself with an Undo on each, and two bugs it could not fix and is holding for the project with a Send button.

## The rules

1. **Counting and checking are not work; fixing is, and it runs on the person's words.** Counts read what the machine already writes, stay on the machine and call no model. Probes only read. Starting a fix needs the person's yes, given once in their own words and kept as a unit they can see, pause and stop in Autopilot.
2. **An idea is a strip the person can ignore, never a popup.** A chip beside the pill while something waits. Nothing on the line, no keyboard, no sound, no mark on the dot, never during a turn. Silence is an answer, and it expires.
3. **A tap is the ask.** Every build an offer leads to is an ordinary turn with its restore point, started by the person's tap on the row (WB rule 7). The agent never builds anything from a count on its own.
4. **It fixes only what it can prove, and it cannot touch what judges it.** A fix lands only when a test that failed before it passes after it, run by a fixed program the fixer cannot edit. What it cannot prove here, it writes up.
5. **Every change has an undo that does not go through what changed.** Bombadil's own code lands as a new generation beside the old one, and a guard outside both puts the old one back if the bar or agentd stops answering.
6. **It works in the gaps.** Only while the person is away, one job at a time, under a written budget, never on a turn's path. Their Enter parks it at the next step.
7. **What the person would notice waits for them; the rest lands quietly and shows with Undo.** What the person sees or types into, and what the agent is told, waits for "Use it". Nothing leaves the machine until they press Submit on a page that shows exactly what goes.

## The loop in four verbs

| | Reads | Makes | The person sees it in | May change |
|---|---|---|---|---|
| **Notice** the person's asks | turns.jsonl and per-turn logs | a count per request, then one offer | the Noticed chip and card | nothing until the person taps |
| **Check** itself | hyprctl, coredumps, the bar's reports, the ledger, app status | a finding per fingerprint | Noticed, then Away | nothing |
| **Mend** what it can prove | a finding's evidence | a tested generation | Watching while it runs; Away and Time after; Noticed for "Use it" | its own words, apps, kit extras and code, never the person's files, the system or its judges |
| **Tell** the project | findings it cannot fix | a scrubbed report | Noticed, "Send to the project" | nothing until the person submits |

## 1. Counting what the person asks

**What counts.** A prompt the person typed at the pill that went to the model: the turns.jsonl rows with no `kind` (main:src/bombadil/agentd.py:540-545), not starting with "!", and not from an app, a coding session, a routine or the loop itself. A near-miss of a launcher word is a counted ask like any other. I ran `launcher.match` on main with an app called Passwords: "show me my passwords", "open my passwords", "passwords app", "password" and "can you open passwords please" all return None, so each is a model turn with a snapshot, while "passwords" and "open passwords" open it locally. Launcher words (`kind:"local"` rows) are not asks; their use only tells the loop which words it made are still used.

**Never counted.**
- "!" lines, and sign-in turns and the duplicate the login branch logs when it re-queues one (login:agentd.py:618-624).
- Asks about private things: the brain's private flag, ~/.ssh, keyrings, a password app's contents. Asks carrying a selection or pasted text over 300 characters.
- Asks from apps (they count as that app's friction), from coding sessions (not in the first version), and the loop's own turns and offer taps.
- A turn the person stopped, that failed, or that they undid within 60 s, and its rephrase within 2 minutes. Those are friction signals for Check (section 4), not habits.
- A request the person said "Never" to.

**What the ledger needs first.** Today a row has no lasting id (the counters restart at 1 on every agentd start, main:agentd.py:67 and 71), no start time, no seconds, no origin, no tool list, no model or cost (Claude's parser drops them, main:providers.py:113-159), and no link to an undo. Local rows carry no verb, and stop is never logged (main:agentd.py:255-260 returns before `_local`). Ledger v2 adds, on model rows: `id` (the per-turn log's stem, `<ms>-<n>`, main:agentd.py:371), `started`, `seconds`, `origin` (typed, button, app, session, routine, loop, retry), `tools` (count and names), `model`, `cost`, `undone_by`; on local rows: `verb`, `via` (typed or button) and `of` for undo; and a row for stop. Old rows still count with what they have. This is the one piece whose delay loses data for good, which is why it is first.

**Same request, without a model.** Four signals, all local:
1. *Text.* `launcher.normalize` and `_key` folding (main:launcher.py:64-73), the "[from app x]" and "About <path>:" prefixes stripped, numbers, paths and URLs turned into slots ("timer <n> minutes"), a fixed filler list dropped ("show me", "can you", "please", "my"), a 30-line suffix stripper and about 40 folded synonyms (eat, hog, use). Compared by trigram overlap.
2. *Verb.* A fixed table of about ten classes: open, ask, make, change, install, tidy, log, fix, find, tell-me-when.
3. *Named things.* The apps, panels and utility words the launcher already knows (main:launcher.py:23-46, 108-124), and the brain's thing titles once it lands. Two asks naming different things never merge.
4. *Route.* What the turn actually did, read from the per-turn log each row names: programs and subcommands and os-mcp tools mapped to topics (memory, disk, network, packages, browser:<host>, app:<name>, files:<area>, docker). The narrator's labels are too coarse for this (`free -h` reads "Checking memory", `ps aux --sort=-%mem` reads "Checking what is running"), so the loop keeps its own topic table.

Route is what joins paraphrases: "what's eating my ram" and "memory hog?" share no words but both ran `free` and `ps`. Two asks join when the verb class or a named thing matches and 0.4 × text overlap + 0.6 × route overlap reaches 0.5, or on text alone at 0.8. A drafting pass tried this on a 40-ask corpus it wrote itself: text alone found 3 of 7 groups, text plus route found all 7 and left the one-offs alone. That shows the design hangs together, not that it works on real use, so the build starts by replaying a real turns.jsonl from daily use.

**Where a model may help.** Only when a group is about to become an offer: one call to the fast model with at most eight of the person's prompts quoted as data, answering a fixed JSON shape `{same, label (4 words at most), form}`. It can split a group or rename it; it cannot add a member, and it never counts. The answer is pinned to the member ids and asked again only when two members change, the brain's rule for descriptions (BB rule 6). With no model available the group stays as counted. At most one such call a day, only while the person is away.

**When a group becomes an offer.** 3 asks on 2 different days within 21 days, and worth building: together they took at least 45 s of turn time, or each ran 3 or more steps, or they were near-misses of an existing word. A one-line answer the person asks daily earns nothing. At most one new offer a day and three a week. If fewer than 3 of the last 10 offers were taken, the bar rises to 5 asks. These numbers are guesses until four weeks of real offers have been counted (shown, taken, "Not now", "Never", ignored), then retuned; they live in the ledger's config, never in a model.

**Decay.** Each ask weighs 1 and halves every 14 days for ranking. A group with no ask for 30 days leaves the list, keeping its counts; after 90 days its text is dropped and only the counts stay.

**Where counts live.** A derived table `asks` (group, label, first, last, days, n, weight, member ids, form, state) in `~/.local/state/bombadil/loop/loop.db`, filled by reading turns.jsonl from a byte offset, so the loop is never on a turn's path. The state directory is outside the restore points, so an undo never rewinds a count. When the brain's agentd side lands, the table moves into brain.db and a group becomes a derived `habit` thing linked to its turns (BB: no model-made links). "Forget what I ask" in the Noticed window erases the table; the words it made stay.

**Raw counts, on demand.** The Noticed window's "What you ask most" lists each group with times, days, last ask, three of the person's own sentences, and what it became. `bombadil loop asks` prints the same. A read-only os-mcp tool, `asks`, lets the agent answer "what do I ask for most?".

## 2. The Noticed widget, and the ask

**Which widget.** A new one, **Noticed**, answering an eighth passenger question: *what has it noticed, and what did it change about itself?* It lives in the right rail, which is the person's, at the top slot, farthest from the pill, because nothing in it is urgent. Right rail from the bottom: Needs you, Away, Machine, Noticed. It is not a row in Needs you (that is the sessions' walk, cannot be hidden and holds only what waits on the person now) and not only an Away row (Away leaves once seen, and an offer should outlive one glance).

**Three faces.**
- *Strip:* the chip "noticed 2" right of the pill. The number counts decisions waiting: offers, changes waiting for "Use it", reports ready to send. It exists only while something waits.
- *Card:* 300 px, titled "Noticed", one line of why ("1 idea · 1 change to look at"), at most three rows, and a last muted line "Lately: 2 changes this week · See all" that opens the Noticed window.
- *Dot:* none. A mark on the dot means it is the person's turn, and an idea never is.

**Strip first.** Unlike the other widgets, Noticed starts as its strip even when its card would fit, and unfolds only when the person hovers the chip (peek), clicks it (kept up), or says "noticed". An idea that takes room by itself is the Clippy failure: Windows' suggested actions were removed, and in a JetBrains study 62% of suggestions shown mid-task were dismissed against 52% engaged right after a commit. The chip is the right size for a question that can wait.

**When it appears and leaves.** It appears when an offer is ripe, a noticeable change is ready, or a report is ready, and never while a turn runs. It leaves when nothing waits. An offer unanswered for 14 days expires into "Not now".

**The row is the offer.** Each offer row is the person's own words with the count and span ("show me my passwords · 4 times on 3 days"), one line of what would happen, one primary button of three words at most, and a quiet "Other ways". There is no yes/no dialog: pressing the button is the ask, and it sends an ordinary turn with its restore point that Now shows as "Make the word my passwords · from noticed". The closing line is the receipt, with Undo. "Show me" on any option opens a typed preview drawn from a template filled with their counts and a real example, with no model.

**How "no" is remembered.**
- *Not now* hides that group until its count doubles or 30 days pass.
- *Never* stores the group's signature in the ledger, so it survives an undo. The group moves to "Said no" in the Noticed window, with Bring back. Three Nevers on one form (say, widgets) stop offering that form.
- *Silence*: 14 days unanswered counts as Not now. Two silent expiries or two Nevers in a row rest all offers for 30 days, and the card says so in one line ("Resting offers until 3 Nov").
- *hide noticed* hides the widget. Counting and checking go on silently, and "show noticed" brings it back.

**Before the desk lands.** The strip is a chip in the bar row beside the pill, which exists today; its card is the Noticed window, a kit app (`share/apps/noticed`, needs the app kit), opened by the chip or the word. When the rails land, the card moves into the right rail and the window stays as "See all".

## 3. What a repeated request can become

Seven forms are offered; two more are done quietly. Each form shows only when its builder exists on this machine.

| | Form | Fits | Example | Built as | Undo | Available |
|---|---|---|---|---|---|---|
| **A** | A word | every ask ended opening or showing the same thing that exists | "show me my passwords" ×4: "my passwords" opens Passwords | a row in words.toml | the row goes | day one |
| **B** | A card on a word | a read-only answer from the machine, slow, with no state | "what's eating my disk" ×4: "disk" draws a bars card | a card recipe: one read-only command, a timeout, a typed layout the shell draws | the recipe goes | when answer cards land (UB) |
| **C** | A widget | an answer that changes within the hour, asked again and again | "how far is batch" ×11: Batch on the desk while it runs | an app with `[widget]` and `Widget.set` (WB) | the app's git | after "widgets the agent makes" (WB) |
| **D** | An app, or more in one the person has | asks that add to or change one set of data | "log a 5 km run" ×7: a Log run box on Tracker | `create_app`, extending the app that holds the data | the app's git | new app day one; extending after per-app git |
| **E** | A routine | the same ask near the same time on 3 days, and it changes something | "tidy my downloads" on three Sundays: Sundays at 20:00 | a `bombadil-*` user timer with the person's words inside, in Autopilot | Stop on its row | after Autopilot (RB piece 4) |
| **F** | A watcher | asks that poll for an event | "is the ISO build done" ×4: tell me when it finishes | a one-shot watcher that speaks through the pill (UB) | Stop | after Autopilot (RB piece 4) |
| **G** | A standing preference | the same correction of style after answers or builds | "shorter" after 5 answers: "Answers: two lines unless I ask" | one line in memory.md through remember | Forget | after remember (RB piece 5) |
| **H** | A kit part | the machine drew the same piece in 3 of the person's apps | a ring meter hand-drawn in Tracker and Memory | a component in the kit overlay (section 6) | the trail row's Undo | quiet, never offered |
| **I** | A fix in Bombadil | the repeats are workarounds of a bug | "close the details" ×3 after it would not close | a finding (section 4) | as a fix | quiet, never offered |

**A word can only open or show.** A words.toml row maps the person's exact phrase to an app, panel, card or desk face; it can never run a shell command, sudo or the model. It is matched after projects, sessions, apps and aliases (the precedent is dev's `dev_names`, dev:launcher.py:206) and never shadows them. At most 30 words; a word unused for 28 days is put away with Bring back. When the thing already has a word ("memory" already opens the Memory app), the offer only says so and makes nothing: "Memory already opens with the word memory. [Got it]".

**The rule that picks the recommended form.** The first test that matches wins:
1. The asks were retries, or were undone or corrected about how Bombadil behaved: **I**, not offered; it becomes a finding.
2. The asks were corrections of style: **G**.
3. Every ask ended opening or showing the same existing thing: **A**.
4. The asks wait on an event ("is it done", "tell me when"): **F**.
5. The asks came near one clock time on 3 different days, and the route changed something: **E**.
6. The asks read something: **C** if it changes within the hour, else **B** with its word.
7. The asks added to or changed one set of data: **D**, extending the app that holds it, else a new app.

Ties go to the smallest new surface and the cheapest undo: A, G, B, F, E, C, D. The card shows the recommended form and "Other ways" lists at most two more that fit. If none fits, there is no offer.

**On day one** only A and a new app (D) can be built: words.toml is new, `create_app` exists in the app kit. Extending an app needs per-app git (UB:104, not built), and the rest wait on the pieces named in the table. The mock-ups show the full menu.

## 4. Finding its own issues

**Signals, all local, none needing a model.**
1. *Crashes.* `coredumpctl list --json=short` filtered to Bombadil's programs (agentd, quickshell, Hyprland, bombadil-app, os-mcp, the browser panel). systemd-coredump ships on Arch; whether its storage survives a reboot on the installed ISO is unchecked. Failed user units and restart counts, once agentd and Quickshell are units (UB:197 asks for that already).
2. *Liveness.* A real connect to agentd's socket and a `ping` answer. The smoke test's `test -S` (main:iso/airootfs/usr/local/bin/bombadil-smoke:65) is fooled by a stale socket. The bar sends `hello` on connect and `alive` every 5 s; today agentd greets clients and only reads commands (main:agentd.py:101-135).
3. *Compositor invariants,* pure functions over hyprctl JSON, run on Hyprland's own events (openwindow, closewindow, activespecial) and once a minute while the person is away:
   - after the person's own Details, `bombadil-details` is the active window within 2 s (smoke:105, today only in the VM);
   - two floating `bombadil-app-*` windows with one position and size on one workspace;
   - a window larger than its monitor, or under the bar's visible rectangle (the bar reports its rectangles; hyprctl sees the whole layer, not the click mask);
   - a monitor under 1024 logical pixels wide;
   - `configerrors` not empty, or the bar layer missing (smoke:67, 68).
4. *The bar's own reports.* After a summon, `focus_ack` if the input has the keyboard within 300 ms; `friction` when Esc is pressed 3 times in 10 s with a drawer or card up.
5. *Turn outcomes, from the ledger.* `ok` false or null; "the OS tools did not start" (main:providers.py:117-119); an os-mcp tool's error rate from the tool and tool_result pairs in per-turn logs; repeated `create_app` "check FAILED" for one missing piece (kit friction); an undo within 60 s; a stop within 5 s; a rephrase within 2 minutes of a failed, stopped or undone turn; a turn three times slower than its group's median. Signed out, a limit or offline are not Bombadil's bugs (login's classifier).
6. *App health.* An app's status.json saying not ok with the process gone, and FATAL lines in its log (kit:src/bombadil/appkit/runtime.py, check.py).
7. *Provider drift.* Stream lines that parse to no known event. This is the tripwire for a vendor CLI changing under Bombadil.
8. *An idle doctor.* `bombadil doctor --live`, the read-only part of bombadil-smoke run as the user: socket, bar layer, config errors, os-mcp lists its tools, the canary apps load. Once a day while the person is away, and after every landing. The smoke test's key-driving and destructive checks stay in the VM.

Words like "still", "won't", "stuck" in the person's prompts never make a finding alone; they join a finding's evidence when a probe fired within 5 minutes.

**From signal to finding.**
- *Fingerprint:* component, rule id and the first error line with paths, pids and numbers stripped (`hypr:apps-stacked`). Same fingerprint, same finding; repeats raise its count and days, which are the numbers the card shows.
- *When it counts:* an invariant after one retry 500 ms later (the smoke test's own retry); a crash on its first sighting; friction at 3 sightings on 2 days, or one sighting plus a probe.
- *Flaky probes:* a probe that flips red to green on retry three times in a week is quarantined and becomes a finding about itself.
- *Evidence,* in `~/.local/state/bombadil/loop/findings/<fp>/`: the probe's expected and observed output, hyprctl clients, monitors and layers with titles removed, the bar's rectangles, the last 40 log lines, the turn row and its tool events, a screenshot of Bombadil's own windows only, versions (build commit, Hyprland, Quickshell, the CLIs, the kit) and the probe id, so `bombadil probe <id>` reproduces it. Kept on the machine.

**The six found by hand in a test VM.** Today the loop is run by hand: someone tests in the VM and writes down what they hit, the findings are passed on, each is fixed in one commit and the VM is rebuilt. Three of the six were fixed that way in the two days before this was written.

| Finding | Status at d9dde3b | Caught locally by | Fixed where |
|---|---|---|---|
| The details drawer would not close | fixed by hand | the drawer probe, plus Esc friction | here, once the room runs Hyprland; else a report with a patch |
| A Super tap left the pill without the keyboard | fixed by hand | the bar's missing `focus_ack` | same as above |
| The finished line covered a new app | open | bar rectangles against the new window | same as above |
| Every new app opened at one spot | open | the stacked-apps invariant | here: placement is Python the tests can prove |
| Chromium's first-run dialog blocked sign-in | fixed in the native sign-in work | only on a fresh profile, so the room or the project's VM | report |
| QEMU's screen stuck at 640×480 | fixed by hand in skel | the monitor-size invariant | a one-tap fix to the person's own hyprland.lua, plus a report, because skel is copied once and the fix never reaches an installed machine |

Since then, on main at 26843d3, the finished-line overlap and the stacked-apps placement are fixed (commits 96546a7, 74440c5 and 528603b).

A seventh, "the launcher smoke check passed for the wrong reason", is a bug in a judge. Findings about the loop's own checks are always reports, because the loop cannot edit its judges.

## 5. Fixing itself

**Who starts it, and why "never on its own" still holds.** The confirmed rule is that the agent never starts work on its own (UB:140, DB:257, WB rule 7). The dev brief already reads a named check as a standing instruction: "naming the check is the standing instruction" (DB:159). The loop follows that reading. Counting and probes are not work, like the welcome line built locally. Fixing is work, and it runs on one standing yes:
- The first finding the loop could fix shows a card that quotes the person's own words from the request that asked for the loop (the mock-up uses an invented example: *"Keep yourself in good shape while I am away."*). One tap, [Start mending], ever.
- That tap writes one user unit, `bombadil-mend.timer`, with the person's words and the budget inside its `[X-Bombadil]` section. It is listed in Autopilot's "machine's own care" group with Pause and Stop (RB rule 5: nothing runs on its own that is not in one list). "stop mending", "pause mending" and "hold everything" work as RB decided.
- Until they tap Start, each fixable finding shows [Fix it], and pressing it is the ask, the shape of the broken-app Fix button (UB:118).
- A mender never splits a goal or runs beside another mender, so the cut on the machine coordinating sessions (DB:213) is narrowed, not dropped.

**How far it reaches.** Four rungs, each tied to what the checks on this machine can prove. *Up to rung 3 it fixes itself; at rung 4 it writes it up.*
1. *Its own words and data:* words.toml rows, card recipes, units it made. Built in an ordinary quiet turn; undone by the recorded inverse.
2. *Its apps and kit extras:* apps it made (with the person's tap), components in the overlay kit. Built in a quiet turn with `check_app` and a screenshot; undone by the app's git or the overlay's inverse.
3. *Bombadil's own code that the gate can prove:* agentd, the launcher, the narrator, os-mcp, the provider adapters, the kit, the skill's generated docs, and shell QML once the room (below) proves it can run the bar. A fenced coding session, the gate, a generation.
4. *What it cannot prove here:* the compositor config, boot, /etc, sudoers, packages, the installer, vendor bugs (Hyprland, Quickshell, Chromium, Mesa), anything no probe reproduces after two tries, and anything touching the loop itself. A report, with at most one suggested patch that is attached and never landed.

A rung grows only when a change merged by a person adds the check that proves it. The first such step is the room: when it can run Hyprland, shell QML moves from rung 4 to rung 3.

**The mender session.** A dev-layer session (DB) with role `mender`, project Bombadil, named for the finding (`apps-stacked`), started by agentd when the budget allows.
- *Its copy:* `~/Projects/.work/Bombadil/<fp>`, checked out at the exact commit the machine runs. The installed tree has no commit stamp and no `.git` today (main:scripts/build-iso.sh:14 copies bin, src, shell and share), so the image gains a VERSION stamp and the loop keeps a clone.
- *Out of sight:* a dot in the Bombadil chip, no window. `Dev.open` takes no task and `_show` always opens a window today (dev:src/bombadil/dev.py:272, 481), so both change.
- *The fence:* DB piece 3 (`setpriv --no-new-privs`, its own slice with a memory limit, its own ports), no os-mcp, no web. Designed, not built.
- *Its permissions are set by its role, not by the flags agentd uses for the person's turns* (`--permission-mode bypassPermissions --dangerously-skip-permissions`, main:providers.py:99-101). Allowed: git, python, pytest, ruff, qmllint, `bombadil-check`, `bombadil-room`. Denied: sudo, ssh, push, the network, and the protected paths. The repository's own `.claude/settings.json` pre-approves `Bash(sudo *)` (line 27), so the mender runs with managed settings that must outrank it (inferred; to verify).
- *Its task is a template* filled from the finding: the evidence directory marked as untrusted data, the probe id, "write the failing test first", "one commit for the test and one for the fix", "do not push". The person's prompts, window titles and page text never enter it.

**The gate.** A program in the fixed part, `/usr/lib/bombadil/fixed/`, root-owned and shipped in the image, which the fenced session cannot write. It judges; the session does not.
- It copies the candidate's product files (bin, src, shell, share) out of the copy, and runs the base commit's own tests from the installed tree plus only the new tests from `tests/new/`.
- The new test must fail on the base and pass on the candidate. No test may be removed, weakened or skipped. No protected path may change: the loop, probes, the gate, the guard, existing tests, sudoers, managed settings, the fence, snapshots, rollback, the installer, CI.
- At most 5 files and 200 lines. A tripwire for added sudo, network calls, `shell=True`, eval, `curl | sh`, new units or /etc files turns the fix into a proposal.
- Tiers: (0) ruff and pytest, 27 s on the dev branch in a scratch run, and neither is in the image yet; (1) the offscreen QML tests, the kit gallery and the canary apps; (2) **the room**: a second agentd and Quickshell on scratch `BOMBADIL_STATE`, `_RUNTIME` and `_SOCKET` paths (main:src/bombadil/paths.py:7-44), the fake provider, the same probes as the live machine, and before and after screenshots; (3) the ISO build and VM smoke, which stay in the cloud as CI on the project.
- A failure goes back to the session once, marked as coming from the check. A second failure makes it a report: the confirmed once-per-distinct-error rule (UB:118, DB:159).
- *The reviewer:* the other provider, read-only, sees the diff, the finding and the gate's output, never the session's reasoning. A "block" turns the fix into a report. With one provider signed in, a fresh read-only session of the same one reviews, and the trail says so.

The room is the one unknown that decides how much it can fix. Hyprland does not start nested in the cloud container (no render node), and whether a second, headless Hyprland starts on a VM's software rendering is unchecked. Without it, focus and layout bugs in the bar stay at rung 4: found, explained and reported with a patch, and proved by the project's VM smoke.

**Quiet or noticeable.** A fix is *noticeable* when its diff touches what the person sees or types into (shell QML, window placement, launcher words, the look of apps or the kit), what the agent is told (system prompt, skill, memory), or anything that leaves the machine. Everything else is *quiet*. Quiet fixes land at the next calm moment and show in Away and Time with Undo. Noticeable ones wait in Noticed as a card with two pictures, before and after, "Checked 4 ways, reviewed by Codex", and [Use it] [Not now]; ready for 7 days, then dropped and kept as a proposal.

**Landing as a generation.** Bombadil's own code today sits at /usr/share/bombadil, copied in by build-iso.sh and linked from /usr/local/bin.
1. agentd (not the session) exports the tested tree to `~/.local/share/bombadil/gen/<sha>/{bin,src,shell,share}` with a manifest: base commit, tree hash, gate results, and what it needs from the system (`requires`).
2. It waits for a calm moment (away, empty queue), flips the `current` link to the new generation, keeps `previous`, and restarts only what changed.
3. `paths.share_dir()`, the kit's `qml_dirs()` (kit:src/bombadil/appkit/engine.py:18-21), the /usr/local/bin stubs and the shell's launcher resolve through `current` when it is blessed, and fall back to /usr/share/bombadil otherwise. A running process stays on the generation it started with and never mixes two.
4. The first generations need one "install this build" (DB:182): an ordinary turn that ships the resolving stubs and a launcher line in place of `quickshell -p /usr/share/bombadil/shell/shell.qml` in hyprland.lua (main:iso/airootfs/etc/skel/.config/hypr/hyprland.lua:9). An installed machine holds its own copy of that file, so this takes the person's tap once.

Packages, /etc, the kernel and boot never change by generation. They are rung 4.

**The guard, and when the pill itself breaks.** `bombadil-guard` is a user service with Restart=always whose code lives in the fixed part, outside every generation. For 10 minutes after a landing it checks every 10 s: agentd answers `ping`, the bar's `alive` is fresh, the bar layer exists, `configerrors` is empty, os-mcp lists its tools within 20 s, a canary app loads. Three reds in a row, a coredump of a Bombadil program, or a failed unit, and it flips `current` back to `previous`, restarts, marks that tree hash bad for good, writes a `kind:"improve"` row, and leaves one line for the pill: "A fix to the bar broke it, so I put the old one back. [Details]". That is the one line the loop may ever put above the pill, because it is something breaking (UB:140). One put-back per landing, then hourly checks for a day, so it cannot flip back and forth. If the guard itself cannot help, the Lifeboat (UB:197: Super+Ctrl+Backspace, and tty2) gains a first line, "Put the last change to Bombadil back", which flips the link from plain shell, not from the tree a bad fix replaced (the reason `bombadil-rollback` is plain bash, main:iso/airootfs/usr/local/bin/bombadil-rollback).

**What reloads, and when.**
- On the next call or spawn: os-mcp, `bombadil`, `bombadil-signal`, the skill and `app_guide`.
- On an app's next start: the kit and overlay components; a Theme change needs the app restarted.
- A restart at a calm moment: agentd (about 1 to 2 s, and the person's conversation survives only once `session_id` is saved, main:agentd.py:66, the five-line fix UB:186 already asks for) and Quickshell (through `systemctl --user`, which needs the units).
- Never by the loop: packages, /etc, kernel, boot.

**Undo.** The trail row's Undo flips `current` back to that generation's parent and restarts the same parts, live, with no model. "undo apps-stacked" does the same. The bare word "undo" still means the machine's last turn only; bare undo takes only `turn:` snapshots (main:src/bombadil/snapshots.py:76), and the loop's landings are not snapshots.

**Local fixes and the project.** A fix made on the person's machine is also a report with its patch: the trail row carries "Send to the project". When an update (RB piece 6) brings a build in which the finding's probe is green, the local generation retires and the row says "Fixed by the project in build 42". If the probe is still red on the new build, the loop re-runs the local fix through the gate on the new base, or turns it into a report if it no longer applies. So the person's machine never drifts far from the project.

**Budget.**
- *When:* away 10 minutes or more (the presence file DB specifies) or locked; on power, or battery above 50%; not on a metered link; never with a turn running or queued; never within 5 minutes of the person's last prompt. Their Enter, or "stop", parks it at the next step with a wrap-up message.
- *How much at once:* one mender and one room.
- *Per fix:* 30 minutes, 40 turns, a watchdog that ends a session with no progress for 10 minutes, 2 attempts per finding, and a dollar cap where the CLI has one.
- *Per day and week:* at most 3 starts a day and 8 a week, at most 2 landings a day.
- *Quota:* start only below half of the provider's 5-hour and weekly windows, once the Claude parser keeps the rate-limit event it drops today. Until then it runs only between 01:00 and 06:00 and the counts above are the budget.
- *Misses:* two in a row (a failed gate or a put-back) pause mending until the person taps Resume on its Autopilot row.
- *The loop's own model calls:* at most 5 a day. If headless CLI use starts being metered apart from the person's plan (announced, then paused), the loop drops to finding and reporting.

## 6. Growing

- **Kit components.** New components go only into an overlay with its own name, `BombadilExtra`, under `~/.local/share/bombadil/qml`, added to `qml_dirs()`. A same-name overlay shadows the whole kit (verified in a scratch venv), so a new name is required, and it can never replace Theme or AppWindow. A component is promoted when the same piece sits in 3 of the person's apps or `check_app` keeps naming one missing piece; it needs an offscreen render, a gallery entry and a generated docs entry, and converts two call sites as app commits with their own undo. At most 2 a week and 12 in all; unused for 60 days, it is archived and still loadable. Moving one into the real kit happens only upstream, through a report with the patch. The skill's "exactly four imports" (kit:share/skills/bombadil-apps/SKILL.md:76) becomes five.
- **Apps.** Only from the person's tap on D, built with `create_app` in an ordinary turn, extending the app that holds the data rather than making a second one. A broken loop-made app follows the once-per-distinct-error rule.
- **Processes.** User units named `bombadil-*` with an `[X-Bombadil]` record (reason, and the ask or finding it came from), NoNewPrivileges, MemoryMax and a timeout, in a `bombadil-loop.slice` with a low CPU weight. At most 8. Each is an Autopilot row with Pause and Stop. A unit that fails twice disables itself and shows "Fix it or stop it?".
- **Words.** words.toml rows as in section 3.
- **Text the agent reads.** The loop writes no skills, notes or "lessons learned": self-written guidance brought no measurable benefit in a recent benchmark of agent skills and can carry unsafe habits forward. A preference (G) goes through remember with the person's tap and shows as words. Component docs are generated from the kit's qmldir, and a test fails when a documented component is missing or an existing one is undocumented.
- **Retiring.** At most 12 loop-made things are active. What goes unused for 28 days (a word, a widget, a unit) is put away with Bring back, and says so in Away once.

## 7. What the person sees of it

**The trail.** One history: `kind:"improve"` rows in turns.jsonl, read in three places.
- *Away,* on the person's return, merges a night's changes into one row: "Changed 2 things about itself [See]", with a muted line each.
- *Time,* in the Brain, shows them beside the person's own turns, in the machine's orange, marked "Bombadil itself".
- *The Noticed window's* "Changed itself" list, newest first, with Undo on each.

An entry reads: "Tue 03:12 · New apps no longer open on top of each other. Seen 5 times on 3 days. 2 files changed, 1 new test, checked 4 ways. [Undo] [Why?]". Why? opens the finding's evidence; "Show the change" opens the diff in the terminal panel only when the person asks.

**While a fix is in flight.** Watching gets a row, "Mending: apps open on top of each other · checking, 3 of 5", with a meter and an × that ends it. The Bombadil chip shows a moving blue dot named `apps-stacked`, and hovering it peeks its last plain line ("The new test fails, as it should. Changing placement."). Nothing on the line above the pill. If the person comes back mid-run, it parks at the next step and Watching says "Mending 1 thing · paused, you're back".

**When the person is asked.** Five moments only: an offer; the one Start; a change they would notice ("Use it"); a report to send; and Resume after two misses. Everything else is a trail row.

**Staying out of the way.** At rest it is invisible. At most one chip, folded like any strip. No sound, no dot mark, no line above the pill except a put-back. Work only while the person is away, at low priority.

**Turning it down or off.** No new word. "hide noticed" hides the widget and holds offers (the desk's own verbs on a widget name). "stop mending" or "pause mending" stops fixing (Autopilot's own verbs on its row). "hold everything" pauses both for a day (RB). The Noticed window has "Forget what I ask" and "Clear what it found". Landed changes stay, each with its Undo.

## 8. Reports

A finding the loop cannot fix becomes `~/.local/state/bombadil/loop/reports/<fp>.md`, held on the machine, and a Noticed row: "Can't fix here: the details drawer takes no keyboard, 3 times on 2 days. [Send to the project]".

- **Preview.** Send opens a card with the exact text and two lists: what goes (what happened, the build, versions, the failing check and its output, a picture of the windows as boxes drawn from their rectangles, the suggested patch if one exists), and what stays (the person's words, files and paths, page titles, screenshots, their name). [Open the issue page] [Not now] [Never for this].
- **Sending.** The browser panel opens the project's new-issue page on GitHub (the project's repository), filled in, and the person presses Submit themselves. No token is stored and `gh` is not in the image. Before opening it, the loop searches for the fingerprint label; if the issue exists, it offers "Already reported (#97): add that it happened 3 more times?". A body too long for a link (about 8 KB, inferred) goes to the clipboard and the page opens empty with "Paste it here".
- **Scrubbing is by construction, not by regex:** the report is built from typed fields with at most three log lines, the GNOME problem-reporting rule that the system gathers and the user never has to clean.

A report reads:

```
Title: Details drawer takes no keyboard (Hyprland 0.56.2) [fp 3c91a0]
Seen: 3 times on 2 days, build d9dde3b, VM
Expected: bombadil-details is the active window within 2 s of opening
Observed: the active window was bombadil-bar (probe drawer-focus, 2 retries)
Picture: the windows as boxes, drawn from hyprctl rectangles
Versions: Quickshell 0.3.1, Claude Code 2.1.283, codex-cli 0.157.1
Tried: a fix passed the unit tests; proving it needs a real Hyprland (VM tier)
```

**The return path is the hand loop, made a feature.** The project's own fixes come one commit per finding, as they do by hand; the update brings the build; the loop sees the probe go green and closes the finding: "Fixed by the project in build 42".

## A week, counted

What the design must hold to in an ordinary week, so it never turns into maintenance:

| | At most |
|---|---|
| Lines above the pill | 0, except a put-back |
| Marks on the dot | 0 |
| Cards raised on their own | 0 (Noticed starts as a chip) |
| New offers | 1 a day, 3 a week |
| Away rows from the loop | 1 per return, only when something landed |
| Taps required | 0; the Start tap happens once, ever |
| New words to learn | 0 |

## Build these first

Two pieces a builder can take first. Both follow the app kit, since the Noticed window is a kit app, and both leave the fixing machinery for later.

### Where the pieces stand (2026-10-01)

| Piece | Status | Evidence |
|---|---|---|
| 1. It notices | In progress, not on `main` | The unmerged work adds `src/bombadil/loop/` (`ledger.py`, `habits.py`, `route.py`, `forms.py`, `offers.py`, `store.py`, `words.py`, `service.py`), ledger v2 rows in `src/bombadil/agentd.py`, the provider meta in `src/bombadil/providers.py`, words in `src/bombadil/launcher.py`, the chip and card in `shell/LoopState.qml`, `shell/NoticedChip.qml` and `shell/NoticedCard.qml`, the window in `share/apps/noticed/`, `bombadil loop asks` in `bin/bombadil` and the `asks` tool in `src/bombadil/mcp_server.py`. Tests: `tests/test_loop_*.py` and `tests/test_noticed_app.py`, on a golden corpus in `tests/fixtures/loop/`. The 90% precision check on real history (`bombadil loop replay`) is a manual step and no result is recorded. |
| 2. It sees itself | In progress, not on `main` | The unmerged work adds `src/bombadil/loop/probes.py`, `findings.py`, `report.py`, `runner.py` and `signals.py`, `bin/bombadil-probe` with `iso/airootfs/etc/systemd/user/bombadil-probe.service`, `bombadil doctor --live` and `bombadil loop report` in `bin/bombadil`, and the bar's `hello`, `alive`, rectangles, `focus_ack` and `friction` in `shell/LoopState.qml`, accepted (with `ping`) in `src/bombadil/agentd.py`. Probe fixtures cover five of the six findings in `tests/fixtures/loop/probes/`; the sixth, Chromium's first-run dialog, is a report by design. Not done: agentd and Quickshell as user units with Restart=always. |
| 3. The room and the gate | Designed | No `bombadil check`, room or fixed part in the tree. |
| 4. The mender, generations and the guard | Designed | No mender, generation or `bombadil-guard` in the tree; it also needs the dev fence, which is not on `main`. |
| 5. Growth | Designed | No `BombadilExtra` overlay; forms beyond a word and a new app wait on their builders. |
| 6. The desk face | Designed | The chip and its peek card beside the pill are part of piece 1. The right-rail card, the Away row and `improve` rows in Time have no code. |

### 1. It notices: the ledger, the counts, the first offers

**What you see:** nothing for a few days. Then the "noticed 1" chip beside the pill, and a card offering a word for a near-miss they keep typing, or a new app for a set of asks no app holds. The Noticed window opens on the chip or "noticed", with what they ask most and what Bombadil made from it.

**Why first:** history cannot be recovered. Every week without ledger v2 is a week of asks with no lasting id, no seconds and no route. It needs no fence, no root and no merged session layer, and it touches nothing under /usr.

**What changes:**
- `src/bombadil/agentd.py`: ledger v2 in `_log` and `_local` (lines 540-545, 278-288), a stop row, `origin` on every row. `src/bombadil/providers.py`: Claude's parser keeps model, cost, usage and the rate-limit event.
- New `src/bombadil/loop/` (`habits.py`, `route.py`, `store.py`): normalise, route topics, group, decay, threshold, the rule for forms, stdlib only, reading turns.jsonl from a byte offset into loop.db.
- `src/bombadil/launcher.py`: words.toml as a table after projects, sessions, apps and aliases (the `dev_names` pattern); "noticed" joins the name list.
- The strip chip in `shell/shell.qml` (`LoopState.qml`, following PillState), and `share/apps/noticed/` as a kit app.
- `bin/bombadil loop asks`; the read-only os-mcp tool `asks`.
- Tests: a grouping test on a golden corpus built from real rows, the form rule, ledger rows, the words table's precedence.

**Effort:** M.

**Before building:** replay a real turns.jsonl from a Bombadil machine in daily use and hand-label 100 pairs; the grouping must reach 90% precision on "same request" (missing some is fine; a wrong merge is a wrong offer). Confirm every row's per-turn log still exists. Check that the fast model answers the JSON shape on both providers.

### 2. It sees itself: probes, findings, reports

**What you see:** "Found" rows in Noticed with a plain sentence, the count, Why? and "Send to the project". A report page opens filled in; the person presses Submit. In a test VM, the six hand findings show up by themselves.

**Why first:** it is the half of the hand loop that is done by hand today: testing in the VM and relaying what was hit. The probes only read, so they are safe before any fixer exists, and they are the judge every later fix is measured against.

**What changes:**
- New `src/bombadil/loop/probes.py` and `findings.py`: the invariants as pure functions over hyprctl JSON, coredumps, turn outcomes and friction, fingerprints, evidence bundles; `bin/bombadil-probe` as a user unit on Hyprland's socket events.
- `shell/shell.qml` sends `hello`, `alive`, its rectangles, `focus_ack` and `friction`; `agentd.py` accepts them and answers `ping`.
- `bombadil doctor --live`: the read-only smoke checks, with a real socket connect.
- `bombadil loop report`: the scrubbed report and the prefilled issue link.
- agentd and Quickshell as user units with Restart=always (UB:197), so crashes and restarts are signals at all.
- Tests: probe fixtures for the six findings; each probe must be red on the known-bad commit (7fcf8c3 for the drawer, 089f181 for the Super tap and the 640×480 screen, main before the app kit for stacked apps) and green on the fixed one.

**Effort:** M to L. Expect merge conflicts in shell.qml with the desk work.

**Before building:** that `hyprctl -j activewindow` right after Details names the class on Hyprland 0.56; which socket events arrive; that the bar can report its rectangles; that coredump storage persists on the installed image.

**Then, in order:**
3. *The room and the gate* (`bombadil check`): useful at once to the project's cloud fixes and the test VM, before any mender exists. Starts with the spike: does a second Hyprland run headless in a VM?
4. *The mender, generations and the guard:* needs the dev fence (DB piece 3), out-of-sight sessions, the saved `session_id`, the VERSION stamp and a clone, pytest and ruff in the image, and the Lifeboat.
5. *Growth:* the kit overlay, and each further form as its builder lands (answer cards for B, the desk's agent-made widgets for C, per-app git for extending apps, Autopilot for E and F, remember for G).
6. *The desk face:* the Noticed card in the right rail, the loop's Away row, and `improve` rows in Time.

## Decisions, confirmed by the owner on 30 Sep 2026

The owner picked 1a, 2a and 3a on 30 Sep 2026 and approved the plan, which confirms the other defaults below as well. The alternatives stay listed for the record.

1. **What can a repeated request turn into?** *Chosen (1a):* any of the seven forms, one recommended by the rule and at most two more under Other ways. The rule is fixed and explainable, and the person still picks with one tap. *Others:* only light forms (a word, a watcher, a preference) and never a new app or widget unless they ask; no offers, only the counts, and they say what to make.
2. **How much does the person see?** *Chosen (2a):* the Noticed chip only while something waits, changes in Away and Time with Undo, and a before-and-after card before anything they would notice. *Others:* less (nothing on the desk; changes only in Time and on "noticed"); more (a line above the pill after each change).
3. **Does it fix things without asking each time?** *Chosen (3a):* the person's own words in the request that asked for the loop are the standing yes, confirmed by one Start tap, then it mends while they are away inside the budget. *Others:* a Fix it tap on each finding; find and report only, never fix.
4. **What counts as the same request?** *Default:* text, verb, named things and route, locally; the fast model may only split or name a group about to be offered. *Others:* exact text only; embeddings (BB cut them); a model judging every ask.
5. **The threshold.** *Default:* 3 asks on 2 days within 21 days, worth at least 45 s or 3 steps or a near-miss; one new offer a day, three a week. *Others:* 2 asks; 5 asks.
6. **Where does it show?** *Default:* a Noticed widget in the right rail that starts as its strip. *Others:* rows in Away plus a page (never on the desk unless they are back from a break); a standing card like the other widgets.
7. **What lands without asking?** *Default:* quiet fixes land and show in the trail; noticeable ones wait for Use it. *Others:* everything asks; everything lands.
8. **Where are fixes proven?** *Default:* on the machine for rungs 1 to 3 (tests, the room, the reviewer); the ISO and VM smoke in the project's cloud. *Others:* everything in the cloud, as today; everything local.
9. **How does a fix land?** *Default:* user-level generations with a guard outside them. *Others:* straight into /usr through "install this build" (DB:182) with undo on restart; a rebuilt ISO.
10. **Who reviews?** *Default:* the other provider, read-only, plus the room. *Others:* none; the person reads the diffs.
11. **Where do reports go?** *Default:* held on the machine, sent as a prefilled issue the person submits. *Others:* filed automatically with a stored token; never.
12. **Words.** *Default:* none new; the widget's name and Autopilot's verbs. *Others:* "hush" to stop everything; "upkeep" as a name for all of it.

## Cut, and why

- **An offer on the line, a popup or a toast.** Suggestion features die from interruption; Windows' suggested actions were removed, and mid-task suggestions are mostly dismissed.
- **A dashboard or score of the person's habits or of its own improvements.** A gauge with nothing wrong (WB), and exactly the thing they would feel they have to maintain.
- **A settings screen with thresholds, or a "self-improvement mode".** Maintenance by another name. The numbers live in the ledger's config and are retuned from real use.
- **The fixer editing its tests, probes, gate, guard or budget.** Agents game the checks they can touch (METR found reward hacking in 39 of 128 of o3's RE-Bench runs; the Darwin Gödel Machine faked its own test logs).
- **A fix with no failing test first.** Nothing to prove it did anything.
- **The machine pushing, opening pull requests or merging.** Landing in the repository stays the person's word (DB rule 7). Reports go out only through their Submit.
- **Fixing the person's projects or documents.** The loop mends Bombadil and what it made, nothing of theirs (DB:155).
- **A model counting, or embeddings for grouping.** Cost and noise; BB cut both.
- **Self-written skills, notes or lessons.** No measured benefit, and they carry mistakes forward.
- **Retrying until green, parallel menders, fixing during the person's turn.** A token furnace, and one turn, one restore point.
- **Screenshots in reports, and auto-filing with a stored token.** Privacy, and nothing leaves without their Submit.
- **Counting coding-session prompts in the first version.** They are a different kind of ask; a session's repeated instruction is its project's check (DB), later.
- **A daily or weekly digest.** A second history beside Away and Time.

## Extends or changes the earlier briefs

- **"The agent never starts work on its own"** (UB:140), **"sessions start only when you ask, watchers run once on your words"** (DB:257) and **WB rule 7:** extended by one standing instruction for the loop's own class of work: the person's own words, confirmed by one tap, kept as a unit in Autopilot, running only while they are away, within a budget. Argued from DB:159, "naming the check is the standing instruction". Counting and probes are passive, like the welcome line built locally. Offers never start anything.
- **"The machine coordinating sessions on its own"** (DB:213, cut): narrowed to one mender at a time, started by that unit, never splitting a goal.
- **"Each tool's own permission mode stays your setting"** (DB): the mender's permissions are set by its role, because it runs unattended.
- **"install this build" is a machine turn into /usr** (DB:182): kept for root parts and for the one-time switch to generations. Bombadil's user-level code lands by generation, with a live undo that needs no restart.
- **"Changes to /usr go back on a restart"** (UB:104): Bombadil's own code now undoes live, by generation. /usr, packages and /etc are unchanged.
- **The broken-app rule, once per distinct error** (UB:118): applied to loop-made things and to the mender.
- **The Lifeboat and agentd and Quickshell as units** (UB:197): become prerequisites; the Lifeboat gains "Put the last change to Bombadil back".
- **"What skips the model: a small exact list"** (UB:110): extended with words.toml, the person's own repeated phrases, exact, after projects, sessions, apps and aliases, able only to open or show; and the widget name "noticed".
- **The desk's rule 1, seven questions:** an eighth, "what has it noticed, and what did it change about itself?". **Rule 3, the largest face that fits:** Noticed alone starts as its strip. **The cut "a card of the day's receipts":** the card's single "Lately" line and the window read the same `improve` rows Time shows, so there is still one history.
- **The brain** (BB): two derived kinds, `habit` and `finding`, no new link kinds and no model-made links; Time shows `improve` rows.
- **Riding** (RB): Autopilot's "machine's own care" gains Mending; "hold everything" pauses it; updates bring the project's fixes and retire local ones; "What broke it" stays about the machine and what the person installed, and Bombadil's own bugs are this loop's; remember is how a preference is built.
- **The app skill's "exactly four imports"** (kit SKILL.md:76): five, with `BombadilExtra`.

Kept unchanged: no popups; one turn, one restore point; bare "undo" means the machine's last turn; nothing is pushed without "ship"; the `desk` tool only when asked.

## Risks and open questions

- **The room is unproven.** Hyprland does not run nested in the cloud container, and a headless Hyprland on a VM's software rendering is unchecked (llvmpipe already sent Hyprland into safe mode once, commit 298ebed). Without it, focus and layout bugs are reports, not fixes, and the six findings would mostly be reports on day one.
- **The fence is not built.** Sudo is passwordless for the user by design and the repository pre-approves `sudo *`. The mender must not run before DB piece 3 and managed settings that outrank the repository's are in place.
- **A root agent can defeat the fixed part.** The machine's own agent has sudo. Hooks and hourly hash checks catch accidents and injected text, not a determined root agent. The fixer itself has no sudo.
- **Metering is moving.** Headless CLI billing was announced and paused. The loop shares the person's plan windows with the pill; it degrades to finding and reporting if that changes. An API-key path is unresolved.
- **Grouping is unproven on real data,** and one person may produce few offers. The precision check before building and four weeks of counted answers decide the numbers.
- **Generations and root undo can skew.** A root rollback can leave a generation needing a newer package; the manifest's `requires` makes the stubs fall back to the system tree, and the fix waits for "install this build".
- **Config split.** skel is copied once at install, so fixes to hyprland.lua never reach an installed machine (the 640×480 fix is an example). Open: split the file into a system part and the person's part.
- **The loop's own bugs** are protected from the mender, so they are always reports and need a person.
- **Privacy.** The ledger holds the person's words; they stay on the machine, only up to eight reach the fast model per offer, and reports carry counts, never words.
- **Reviewer independence** is weak when one provider is signed out, and models of one family share blind spots.
- **Open for the owner, later:** should other people's Bombadils ever report to the owner's repository? Should anyone but the owner default to find-and-report only?

## Checked, and what still needs checking

Checked on main at d9dde3b:
- `launcher.match` near-misses: "show me my passwords", "open my passwords", "passwords app", "password", "can you open passwords please" return None; "passwords" and "open passwords" open the app.
- turns.jsonl model rows have no `kind` and no id, start time, seconds, origin or cost (agentd.py:540-545); local rows at 278-288; stop is not logged (255-260); counters restart at 67 and 71; per-turn logs are `<ms>-<n>.jsonl` (371); `session_id` is memory only (66).
- Claude runs with bypass flags (providers.py:99-101) and its parser keeps no model, cost or rate-limit event (113-159).
- Bare undo takes only `turn:` snapshots (snapshots.py:76).
- build-iso.sh copies bin, src, shell and share to /usr/share/bombadil with no commit stamp (line 14); hyprland.lua starts agentd and Quickshell with `hl.exec_cmd` (lines 8-9) and holds the Virtual-1 rule (line 5).
- bombadil-smoke checks the socket with `test -S` (65), config errors (67), the bar layer (68) and the drawer's keyboard (105).
- `.claude/settings.json` pre-approves `Bash(sudo *)` (27); sudoers gives the user NOPASSWD.
- On the dev branch, `Dev.open(tool, project, role)` takes no task and `_show` always opens a window (dev.py:272, 481); `match` takes `dev_names` (launcher.py:206).
- On the kit branch, `qml_dirs()` lists the checkout then the installed kit (engine.py:18-21); the skill asks for exactly four imports (SKILL.md:76); `check_app` runs Commands for real (runtime.md:111-113); a same-name overlay shadows the whole kit (checked in a scratch venv).
- Nested Hyprland does not start in the cloud's docker desktop test (the desk build, 29 Sep).

Still to check before building: a headless Hyprland in a VM; managed settings outranking the repository's allow list; coredump storage on the installed image; `hyprctl activewindow` right after Details; the bar reporting its rectangles; the issue-link length limit; that the fast model answers a fixed JSON shape on both providers; that Hyprland 0.56 starts user services with its environment imported.

# The poor man switch

> **Status:** In progress. Piece 1 (Resting) has a first build on a branch that is not on `main`; piece 2 (the finder) and pieces 3 to 8 are designed and have no code; piece 9 is not built, by decision.
> **Code:** none of the pieces is on `main`. They change `src/bombadil/agentd.py`, `src/bombadil/providers.py`, `src/bombadil/launcher.py`, `src/bombadil/appkit/native/agent.py`, `bin/bombadil`, `shell/PillState.qml`, `shell/Stone.qml`, `shell/shell.qml` and `shell/QueueChips.qml`. The unmerged build of piece 1 adds `src/bombadil/rest.py` and `shell/AiCard.qml`; the files piece 2 would add (`src/bombadil/finder.py`, `shell/FoundChips.qml`) exist nowhere yet.
> **Pieces:** piece 1 (Resting: the machine knows it is out, holds your asks, and has a switch) is in progress. A first build is on a branch that is not on `main`: the access state `resting` and the pause and resume words (`agentd.py`, `launcher.py`), the limit parse (`providers.py`), `src/bombadil/rest.py` for `rest.json`, `Agent.ready` and `Agent.note` in the kit's `appkit/native/agent.py`, the resting face (`PillState.qml`, `Stone.qml`), the AI card (`shell/AiCard.qml`) and tests in `tests/test_rest.py`, `tests/test_limit.py`, `tests/test_agentd_rest.py`, `tests/test_launcher_rest.py` and `tests/test_rest_qml.py`. That build is a checkpoint and some of its tests fail. It departs from this brief on three points: `bombadil ask` prints the resting line and exits with status 75 instead of waiting; the resting line of a timed limit has no "Use Codex until" button (the borrow is piece 4); and the AI card rows carry no percentage (piece 3). Piece 2 (the finder) is designed: there is no `finder.py` on `main` or on any branch. Pieces 3 to 8 are designed; of piece 3 the unmerged build only passes the CLI's `rate_limit_event` on as a `meta` event and shows no warning line. Piece 9 is not built, by decision (question 2). There is no `AskButton` component in the kit, on `main` or in the unmerged build; `Agent` on `main` has no `ready` or `note`. The rows of "What keeps working" and "What happens to" that name the Brain, mail, your words (`words.toml`), the Noticed loop, coding sessions and the desk's Away, Machine and Alive describe pieces that are designed or built on branches that are not on `main`.
> **Design:** this brief and its page, [The poor man switch](pages/the-poor-man-switch.html); the pieces it changes are described in [agentd](../architecture/agentd.md), [shell](../architecture/shell.md) and [app kit](../architecture/app-kit.md)
> **Decided by:** the owner confirmed on 2026-10-01 the three decisions under "Questions, and the answers" (1a, 2a and 3a, the recommended option each time); every other fork is a default under "Decisions I picked a default for"
> **Verified:** 2026-10-01 against `main` at `26843d3`: the status above, the file and line references in the text and under "Checked today" (the line numbers are those of `26843d3`), and that on `main` nothing handles `rate_limit_event`, no holding card, no finder and no `AskButton` exists. The unmerged build of piece 1 was read and its tests run. The vendor and tool facts under "Checked today" (the Claude Code 2.1.286 binary and the Codex source, dated 30 Sep 2026) and the figures about local models were not re-checked.

You are watching Bombadil build a habit tracker when a line appears once above the pill: "Claude 5-hour limit at 84%, resets 15:00." Forty minutes later, halfway through the tracker, the stone stops rolling and goes hollow and grey. The line says "Claude hit its limit partway, after changing 2 files. It carries on at 15:00.", with Undo and Details under it as after any turn that changed something. When you dismiss it, the line reads "Claude is at its limit until 15:00. Your apps and files still work." The tracker ask sits beside the pill as a grey chip whose label reads "15:00" instead of "next", and the empty field says "Open or find anything. Asks wait for 15:00."

Nothing else stops. You type "passwords" and Passwords opens. The browser is still signed in to Gmail, the ISO download on the Watching card keeps counting, and "!df -h" still runs. In your Notes app the Summarise button shows "At 15:00" under its name, and pressing it adds one more chip. You type "the march invoice". Nothing fails: the line says "Kept for 15:00. Found on this computer:" with three chips, the invoice PDF, your Tracker app, and "You asked: file the March invoice (3 Sep)". You press the PDF and it opens. At 15:00 the stone fills green, the line says "Claude is back. Running your 3 waiting asks.", and the tracker carries on in the same conversation, told what it had already written.

On Sunday evening, with most of the week's plan used and two coding sessions you want to keep running, you click the stone. A small card rises with one row per AI: "Claude · ready" with a switch, "Codex · not signed in". You flip Claude's switch off. The stone goes hollow, the line says "Claude is paused. Your apps and files still work.", and nothing the machine does by itself asks Claude until you flip it back or type "resume Claude". At the end of the month it is the organisation's spending limit instead: "Claude is at its spending limit until 15:00. Your apps and files still work.", with one button, Raise the limit, which opens the usage page in the browser panel and buys nothing for you.

This design pass answers the owner's ask of 30 Sep 2026 for "The poor man switch": a toggle that lets the person keep using the OS, the apps that were created and their processes when the AI account runs out of tokens, so that the OS is not useless then. The owner asked for ideas and for how it would look. It sits on the earlier briefs (How Bombadil should feel, Building on Bombadil, Bombadil's Brain, The Bombadil Desk, Riding with Bombadil, Bombadil's voice, Bombadil tends itself, Bombadil installed, Bombadil at work, The Bombadil mark), whose decisions stay in force; where a piece here extends one of them, the change is named near the end. Both kinds of limit are real: a plan's weekly limit and an organisation's monthly spending limit each stop every session until the reset. The design was made from four research sweeps (what in the code needs the model, how the two command-line tools report running out, whether a local model could stand in, and what the earlier briefs already decided), three independent drafts (a person's day, how much can survive without a model, what the code allows this week) and two critiques (truth against the code and the binaries; the project owner's own rules), merged with every blocking finding applied. Code references are to main at 26843d3, which includes the app kit, the shared theme, the stone in the pill and the pictures of the machine. "Unverified" marks anything nobody checked.

References: file names without line numbers point at the other briefs in this folder; Code references (`agentd.py:1164`) are line numbers on main at 26843d3 and drift as the code changes; the function names beside them stay findable.

## The short answer

**Most of the OS already works without the model. What makes it feel useless is that it does not know it is out. Make running out a state: say it once with the time Claude comes back, hold every ask instead of failing it, find things on the computer when you type a sentence, and give you a switch to flip it yourself.**

- **Already true today, with no model:** apps and their processes, background jobs, the browser and its sign-ins, files, the terminal, launcher words, "!" commands, undo, history, Details, stop, pictures of the machine, and the desk's Watching card.
- **What is broken today:** a limit changes nothing. The stone stays green, the field says "Ask anything", Enter shows "On it", a red line fades after 12 seconds, and every waiting ask is still sent into the wall. Each one fails the same way, takes a restore point and clears the undo marker (agentd.py:1164-1166, :1241-1254). The limit words miss most of today's wordings ("session limit", "weekly limit", "out of usage credits"), and they wrongly catch the throttle notice that says "not your usage limit" (agentd.py:139).
- **Build two things first.** (1) Resting: one new state in agentd that turns on when Claude or Codex refuses an ask for a limit, holds every ask (typed or from an app) as a chip labelled with the time, comes back by itself at the reset, and can be flipped by hand with a switch in a small card under the stone or the words "pause Claude" and "resume Claude". (2) The pill still answers: while resting, a sentence also gets up to three chips of things found on the computer.
- **No small AI on the computer for now.** The test VM has 5 GB of memory and no graphics card; a model that fits would still pick the wrong thing about one time in ten. Finding things gives most of the benefit for nothing (question 2).
- **Moving to Codex and spending money stay one press away** and never happen by themselves.

## The rules

1. **Nothing you made stops when the AI does.** Apps and their processes, background jobs, the browser, files, launcher words and "!" commands never check whether the AI is there. This is the first brief's rule that recovery never goes through the part that broke (ux-brief.md), applied to a plan that ran out.
2. **Running out is a state, said once, with the time it comes back.** One line names the AI and gives the time the provider sent, never a guess (voice-brief.md), and reads the same in every voice (voice-brief.md). One button per case.
3. **An ask made while the AI rests waits; it never fails.** It waits as the grey chip the queue already uses, labelled with the time it will run, and × removes it. The same holds for an app's ask.
4. **One refusal is enough, and nothing spends while it rests.** After the first refused ask nothing more is sent until the reset, your press or your word. No test calls, no retry loops, and a refused turn never costs you the conversation or a real undo point.
5. **A sentence still gets an answer from the machine.** While the AI rests, the pill shows what it found on the computer, and you press. It never guesses and acts (ux-brief.md; passenger-brief.md, "Records answer before models").
6. **Money and the other account wait for your press.** Raising a limit, buying credits and moving to Codex are each one press; the machine never does them alone.
7. **Resting is nobody's failure and nobody's turn.** Not red, not the amber knock, never on the desk (widgets-brief.md), never a finding (self-improvement-brief.md), never a failed routine run (passenger-brief.md).
8. **The switch you flip and the switch that flips itself are the same state.** A hand pause looks exactly like a limit, so there is one thing to learn, and every line in this brief can be tried on the VM without spending a real limit.

## Build these first

These two make the machine stop pretending and keep it useful while it waits. Both reuse what already runs for the signed-out and offline states.

### 1. Resting: the machine knows it is out, holds your asks, and has a switch

**What you see:**
- The first refused ask turns the stone hollow and grey (a stand-in until the mark's design draws it). The cut-off turn's closing line says how far it got, with today's Undo and Details if it changed something; then the resting line for its case takes over, in tone "step", not red (the table under "The pill while the AI rests").
- One button under the line: "Use Codex until 15:00" on a timed limit if Codex is signed in; "Raise the limit" on a spending limit, which becomes "Try again" once pressed; "Resume Claude" after a hand pause.
- Every waiting chip's label changes from "next" to the time ("15:00", "Thu 09:00"). Anything you type that is not a launcher word or a "!" command becomes such a chip, and "On it" never shows for it. The empty field reads "Open or find anything. Asks wait for 15:00."
- Apps: an ask from an app waits as a chip ("from Notes: summarise this"); an app's ask that was cut off goes back into the queue with no error text, and its answer arrives after the reset. A kit button that asks the agent shows "At 15:00" under its label and stays pressable.
- At the reset (plus one minute, on the wall clock) the stone fills green, the line says "Claude is back. Running your 3 waiting asks.", and the cut-off ask runs first in the same conversation, with a note of what it had already changed.
- The toggle: clicking the stone while nothing runs opens a small card with one row per AI ("Claude · ready", "Claude · at its limit until 15:00", "Codex · not signed in"), each with a switch. Flipping Claude's switch off is the same as typing "pause Claude"; flipping it on is "resume Claude". The card shows a percentage only once Claude's own tool has sent one (see "The switch"). While a turn runs, the stone is still Stop.

**Why first:** this is where the uselessness is. Today a limit only rewrites the error text (`_limit_text`, agentd.py:1499, called at :1464), so the next ask and every waiting ask fail again, each taking a restore point and clearing the undo marker (agentd.py:1241-1254); in the unmerged VM fixes, which keep 30 restore points, one bad afternoon pushes real undo points out. The line lies 12 seconds after the limit. Apps get "Error: This account has hit a usage or spending limit…" pasted into their reply (appkit/native/agent.py:109-111, :113-118). And the owner asked for a toggle.

**What changes:**
- **src/bombadil/providers.py.** `Claude._events` (:254) turns `rate_limit_event` into `{"kind": "meta", "rate_limit": <rate_limit_info>}`, the shape the self-improvement branch already uses (its providers.py:307-309), so the two merge cleanly. An assistant message whose `error` is `rate_limit` or `billing_error` yields a `limit` event next to the `authentication_failed` branch (:282). The result mapping (:299) carries `api_error_status` and `api_error`. `system/api_retry` with a 429 or 529 becomes a line-only `retry` event, so the watchdog (agentd.py:1352) says "Claude is busy, trying again" instead of "not answering; check the connection". `Codex.parse` (:474) folds the U+2019 apostrophe and marks a `turn.failed` matching "hit your usage limit", "out of credits", "spend cap" or "quota exceeded" as a limit, with the time read from "try again at 3:45 PM" or "Oct 2nd, 2026 3:45 PM" (local time; Codex error.rs:818-829). The limit patterns live next to the signed-out patterns with tests from captured output, as ux-brief.md asks.
- **src/bombadil/agentd.py.**
  - A new access state `resting` beside choose, checking, signed_out, offline, signing_in and ready. Because it is not ready, `_runnable` (:1164) already holds every model ask and lets "!" through, and launcher words are matched before anything is queued (:283). The gate needs no change.
  - `_on_event` (:1412) classifies a failed, non-"!" turn as a limit only when all of these hold: `api_error_status` is 429 (or `terminal_reason` is `api_error` with an assistant `rate_limit`/`billing_error`); a `rate_limit_event` from this turn says `rejected` with `isUsingOverage` false, or the text has quota wording; `api_error` is absent (it marks entitlement checks such as `model_requires_usage_credits`, which are not a used-up quota); and the text is not the throttle ("not your usage limit") or the "high load" notice. Codex: the patterns above, never its retryable "rate limit exceeded:".
  - The reset time: `resetsAt` of the window when it is in the future, else `overageResetsAt`, else the time in the text ("resets 3:45pm", "resets Mon 12:00am"), else none. Spending limits usually come with the window's time ("your session limit resets 3:45pm"), so "no time" is rare.
  - On a limit, `_rest_turn` (modelled on `_signed_out_turn`, :1143) puts the ask back at the front of the queue under its own turn id, restores the notes (as :1157 does), adds a note of what the turn had already changed (from the narrator's touched list), and keeps the session id: a limit never drops the conversation, whatever `num_turns` says (today :1465 would drop it at 0; unverified what a refusal carries). `turn_end` for that turn carries `"requeued": true`.
  - A refused turn that changed nothing puts back the undo marker that :1249 cleared, so the next "undo" skips its empty restore point instead of saying "Undone" and changing nothing (the old bug at ux-brief.md). No new sudo operation is needed.
  - `_wait_reset`, shaped like `_wait_online` (:988) but never calling `start_signin`: it sleeps at most 60 s at a time and compares wall-clock time with `until`, so a computer that slept through the reset still wakes right, then calls `_set_access("ready")`, which wakes the worker (:913-920). The first waiting ask is the check; if nothing waits, nothing is sent. If that ask is refused again with a time already past, it waits 5 minutes before the next try.
  - `check_access` (:922) does not overwrite `resting` while `until` is in the future or a hand pause is on; today it says ready whenever credentials are stored, and an account at its limit still has them. A completed sign-in to a different account clears a limit rest.
  - `signin_asked` (:947) protects a working login in `resting` as it does in `ready`: typing "sign in" while resting must not start `codex login`, which revokes the stored login (`login_replaces`). A cancelled sign-in started from `resting` does not drop the held asks (`_drop_waiting`, :1036), and `open_url` of an OAuth page does not lose the rest.
  - `_describe` (:875) gets the resting lines and the one button per case; `_setup_msg` (:866) adds `until`, `why` (limit, spend, hand) and `kind` (five_hour, seven_day, …). `setup_action` (:1078) gains `resume`, `retry` and `raise`; `raise` opens the page the provider's message names, else claude.ai/settings/usage (Claude) or chatgpt.com/codex/settings/usage (Codex), through `open_url` (:1090). `_status` (:356) adds `rest`.
  - `LIMIT_WORDS` (:139) becomes a text fallback that also catches "session limit", "weekly limit", "opus limit", "sonnet limit", "shared budget", "out of usage credits", "spend cap", "out of credits" and "quota exceeded", and drops the throttle; `RATE_WORDS` (:141) gains "not your usage limit".
  - A small `src/bombadil/rest.py` reads and writes `paths.state_dir()/rest.json` (paths.py:25): `{"hand": {...} or null, "providers": {"claude": {"why", "kind", "until", "since"}}}`. It lives in the state directory, not the runtime one, so a weekly limit and a hand pause survive a reboot, and it is the one contract other processes read (the Brain, the self-improvement loop, coding-session dots). Entries whose time has passed are dropped on read.
  - At most one waiting ask per app: a newer ask from the same app replaces its older chip, so an app on a timer cannot fill the queue.
- **src/bombadil/launcher.py.** Next to `PROVIDER_WORDS` (:50): "pause claude", "pause codex", "pause the ai" and "resume claude", "resume codex", "resume the ai". "use claude" also resumes. They follow the passenger brief's "pause <name>" grammar (passenger-brief.md) and are matched before anything is queued.
- **src/bombadil/appkit/native/agent.py** (now on main). `handle` (:113) reads `setup` and `rest` from status into two properties, `ready` and `note` ("At 15:00", "Paused", "At its spending limit"). A `turn_end` with `requeued` keeps the turn waiting and fires no `replied`; the later real `turn_end` fires it with the answer.
- **The shell.** shell/PillState.qml: `setupState` (:24) gains `resting`; `face` (:73) gains a `resting` value that is never `needs`; in `turn_end` (:196), when the turn was refused for a limit and changed nothing, show the resting line in tone "step" instead of the red error; a `setupUntil` property feeds the placeholder (shell.qml:410) and the chips' label (QueueChips.qml:32). shell.qml: the stone's TapHandler (:397), which is enabled only while stoppable, also opens the AI card at rest. A new shell/AiCard.qml draws the rows and switches (it is the first row of the passenger brief's holding card, passenger-brief.md, which nobody has built yet).

**How it is tested:**
- **Fake provider, tests/test_agentd.py.** `Scripted` (:59) prints a `rate_limit_event` with `status: "rejected"`, `resetsAt` and `rateLimitType: "five_hour"`, an assistant message with `error: "rate_limit"` and "You've hit your session limit · resets 3:45pm", and a result with `is_error: true`, `api_error_status: 429`, `terminal_reason: "api_error"`. Asserted: state `resting` with `until`; the ask is back first under its id; a second ask queues without starting; "!echo hi" runs; "files" is answered locally; the session id is kept; with `until` moved into the past, the asks run in order on the same session. The signed-out rerun test (tests/test_agentd_signin.py) is the template.
- **Negative tests:** `status: "allowed"` with `overageStatus: "rejected"` (the self-improvement fixture's shape, a healthy account with org-disabled credits); `status: "rejected"` with `isUsingOverage: true`; `api_error: "model_requires_usage_credits"`; the throttle "Server is temporarily limiting requests (not your usage limit)"; the "high load" notice; `terminal_reason: "blocking_limit"` (the context window); `error_max_budget_usd`; a 529; Codex's "rate limit exceeded:"; a "!" command that prints "rate limit". None of them rests. The existing tests at :2145-2175 stay green.
- **Codex:** `ScriptedCodex` sends the curly-apostrophe message with "Try again at 3:45 PM" and a second case with "Oct 2nd, 2026 3:45 PM".
- **Hand words and the card:** "pause Claude" rests with `why: hand`, a new agentd on the same home is still resting, "resume Claude" returns to ready; the "sign in" word in `resting` does not start a Codex login.
- **Apps:** the kit's agent test feeds a requeued `turn_end` (no `replied`) and then the real one (`replied` with the answer), and a status with `setup: "resting"` (`ready` false, `note` "At 15:00").
- **Offscreen QML:** tests/test_pill_qml.py injects resting `setup` messages and checks the line, the button, the placeholder, the time labels, no "On it", no red, and the card's switch sending the pause.
- **Headless desktop:** tests/desktop/fake_api.py only answers 200; add a script that answers 429 with `x-should-retry: false` and the `anthropic-ratelimit-unified-status: rejected`, `-reset` and `-representative-claim` headers. That login probably uses an API key, which gets no `rate_limit_event` (research: the event exists only for claude.ai logins), so this test proves the 429 path and the fake provider proves the event path.
- **Test VM:** "pause Claude" walks the whole look for free; `desk.inject` shows any line; the same 429 script in scripts/vm-tools/scratch_api.py covers the real CLI. At the next real limit on a long-lived test VM, the stream is saved as tests/fixtures/claude-limit.jsonl.

**Effort:** M.

### 2. The pill still answers: things found on the computer

**What you see:** While the AI rests, a sentence that is not a launcher word is kept as a chip, and the line also shows up to three chips of things on the computer that match it: an app (by its title and the description the agent wrote for it), a launcher word or one of your own words you nearly typed, and a past ask of yours with its date ("You asked: file the March invoice (3 Sep)"), which opens that turn's Details. Once the Brain merges, files and pages join them. The line reads "Kept for 15:00. Found on this computer:" or "Kept for 15:00. Nothing on this computer matches." Pressing a chip opens the thing and drops the kept ask it came from. Nothing opens by itself and nothing runs.

**Why first:** a launcher that answers only exact words makes every other sentence a dead end, which is exactly the "useless" the owner fears. Finding is free, works offline, and keeps the ux brief's rule: the machine shows, you press (ux-brief.md; brain-brief.md).

**What changes:**
- **A new pure module, src/bombadil/finder.py**, which never calls a model or the network. `find(text)` ranks by word overlap and prefix over apps (apps.py keeps `title` and `description`), `launcher.entries()`, words.toml once the self-improvement branch lands, and successful past asks from turns.jsonl (prompt, closing line, details path; agentd writes them, watch.py reads them). Later, the Brain's FTS5 search (store.py in the Brain's unmerged work) and each app's "does" rows (piece 6).
- **agentd:** when an ask waits because the state is not ready, the finder runs in a thread and broadcasts `{"type": "found", "turn", "matches"}`. A press sends the existing `local` message, or `details` for a past turn.
- **The shell:** a FoundChips.qml shaped like SetupChips.qml, under the line.

**How it is tested:** unit tests for the ranking on a fixture turns.jsonl and a fixture ~/Apps; the offscreen QML harness injects a `found` message; the headless desktop test fakes a limit with piece 1's script, then types "passwords" (opens) and "my password app" (offers Passwords).

**Effort:** S without the Brain source, M with it.

### Why these two first

Piece 1 stops the damage: the false screen, the failing queue, the burned restore points, the error text in apps, the lost conversation. It gives the owner the toggle that was asked for, and it gives every other piece one signal to read (rest.json and the status message). Piece 2 is the difference between "waiting" and "still useful", at no cost. Everything after these reads piece 1's state rather than inventing its own.

## After these, in order

**3. A warning before the wall, and the last of the window kept for you.** When `rate_limit_event` first reports `status: "allowed_warning"` in a window, the closing line adds the dev brief's own words with the CLI's own numbers: "Claude 5-hour limit at 84%, resets 15:00." (dev-brief.md). With Codex signed in, "Use Codex until 15:00" is offered there too (passenger-brief.md). At the same moment a silent flag in rest.json tells the machine's own background model users (Brain descriptions, the Noticed loop, menders, mail's "Needs a reply" marks, routines that ask the AI) to wait until the window resets, so what you type still gets through. When the account is running on paid usage credits (`isUsingOverage`), the line says once "Claude is using your usage credits until 15:00." and the same flag keeps background use off them. The public `utilization` field is set only with a warning; the CLI warns at 90% of a 5-hour window used in under 72% of its time, and at 75%, 50% or 25% of a weekly window used early (from the binary), or at a threshold the server picks, so this brief promises "a warning when Claude warns", not fixed 80% and 95% lines. No ring on the stone. Codex's exec output carries no usage numbers, so Codex shows only the wall. Effort S.

**4. The other AI, and the other model, until the reset.** "Use Codex until 15:00" runs a borrow, not today's `choose` (agentd.py:1064), which saves the provider for good and drops the conversation: it keeps Claude's session aside, runs the waiting asks on Codex in a fresh conversation (a conversation does not move between vendors, dev-brief.md), switches back at the reset, restores Claude's session and hands it what Codex did as notes. Separately, when only one model family's weekly limit is hit (`seven_day_opus` or `seven_day_sonnet`), the machine moves to the other family by itself and says once: "Opus is at its limit until Thursday 09:00, so Sonnet answers until then." It costs nothing, keeps the conversation, and "use opus" takes it back; it needs the model setting of the unmerged VM fixes and "use sonnet". Effort S to M.

**5. Offline and outages take the same shape.** Today a turn that loses the network says "not answering; check the connection" (agentd.py:1352), and the `offline` state only covers reaching the sign-in page; when the network returns, `_wait_online` starts a sign-in (:998-999), which would end a working Codex login. New reasons `offline` and `outage` enter the resting path: offline comes back through a reachability poll that calls `check_access`, never `start_signin`; an outage (an assistant `overloaded` or `server_error` after the CLI's own retries) tries the first waiting ask again after 2, 5 and 10 minutes, then every 15. Offline keeps its own words and look (today's setup offline shows the amber "needs" face with "No internet…", agentd.py:886-889; whether that should change is for the mark's and the desk's designs to settle). Effort S to M.

**6. Apps teach the pill.** When the agent builds an app, it also writes a few `[[does]]` rows in app.toml: a plain sentence ("Add a login"), other ways to say it, and an entry point (`bombadil-app run passwords --do add`). The finder matches them, so "add a password for the bank" offers "Passwords: Add a login" with no model at all, and Tab offers them when the AI is there too. Only Bombadil can do this, because the builder of every app also writes its instructions for the pill. Effort M, in the app kit's own work.

**7. Things you made keep going with no turn.** The Watching card gains "Run again" for a finished job, from the command its record keeps (jobs.py:322); today only the agent can start a job, inside a turn. Accepting a Noticed "word" offer writes the words.toml row locally instead of running a turn. And, since question 3 was answered a, recipes: when a task ends well using only shell commands and is the kind people ask again ("fix the sound", "restart the VPN"), the agent may keep it under that name; later the name runs those exact commands as a "!" turn with a restore point, with or without Claude, and the line says "Ran your recipe 'fix the sound' (kept 12 Sep)." Only recipes whose every step undo can reach are kept: a step the narrator marks irreversible (deleting files in your home, formatting a disk) makes the task ineligible. Effort M.

**8. A number for the card without a turn.** The card shows a percentage only after a warning. For a number at any time the candidates are `claude -p "/usage"` (documented as a local command whose output comes back as a message, no model call; not run here) and the experimental `get_usage` control request; for Codex, the newest rollout file's `token_count.rate_limits`. The undocumented usage endpoints stay cut. Effort S, after the check.

**9. A small local helper. Not built: question 2 was answered a.** Kept only as the record of what was weighed, to reopen if that answer changes. llama-cpp's `llama-server` as a per-user service (the Arch package ships the user unit; about 34 MB with ggml), a 1-2B model held to a JSON schema that picks one entry from the finder's list or launcher words; when unsure it shows "Did you mean Files?" and never acts. Reached from the resting line, as the passenger brief planned for the offline line (passenger-brief.md). Effort L.

## The switch

**Its name.** "The poor man switch" is the name in the briefs and in anything written to the owner. It never appears on screen: the screen says what is true ("Claude is at its limit until 15:00", "Claude is paused"), people search for and paste those sentences (voice-brief.md), and someone stopped by a team budget is not poor. In the code the state is `resting`, with a reason: `limit`, `spend` or `hand` (later `offline` and `outage`). The word "tokens" appears nowhere; "limit" is the providers' own word and the one ux-brief.md chose.

**Where it lives.** In agentd as the access state `resting`, mirrored in `~/.local/state/bombadil/rest.json` for other processes. On screen: the stone, the line, its one button, the placeholder, the time on the waiting chips, and the switch on the AI card that opens when you click the stone at rest. Nothing on the desk or the bar.

**It flips on by itself** only when a real ask is refused for a limit (the conditions under piece 1). Claude's `rate_limit_event` alone never flips it: the CLI re-sends a rejected event every 30 seconds and may send a cached one before a turn starts (passenger-brief.md records it arriving before `system/init`), and a window can read `rejected` while paid credits carry on (`isUsingOverage`). The event supplies which limit and when it resets; the refusal decides. A busy moment never flips it: a 529, an `api_retry`, the throttle notice, Codex's retryable "rate limit exceeded:", or the context window's `blocking_limit`.

**It flips on by hand** with the AI card's switch, or "pause Claude", "pause Codex", "pause the AI". A hand pause lasts until you flip it back, across restarts, and the line says so at every start, so a forgotten pause is never a mystery. It stops the pill's asks, apps' asks and the machine's own background use of the AI; coding sessions you opened yourself keep running.

**It flips back:**
- **with a time:** one minute after it, on the wall clock, with no test call; the first waiting ask is the check, and if it is refused again the state returns with the provider's new time;
- **with no time** (rare: a spending limit with no window time, some Codex plans): with Raise the limit, which becomes Try again once pressed, or "resume Claude";
- **after a hand pause:** the switch, "resume Claude", "use claude" or the Resume Claude button;
- **always:** a completed sign-in to a different account clears a limit rest, and "use codex" moves to a provider that is not resting.

**Per provider, per model family.** The 5-hour and weekly limits cover every model of that account; the Opus and Sonnet weekly limits cover only that family (Claude Code's errors.md). So the rest is kept per provider, and a family-only limit moves to the other family instead of resting (piece 4). If both providers are out, one line gives the earlier time.

## The pill while the AI rests

**When the line shows:** the moment the state flips, whenever you summon the pill with Super, and whenever you type an ask that has to wait. Otherwise it fades after 12 seconds and the empty field carries the state. It outranks the greeting, which is dropped (voice-brief.md).

**The words** are proposals for the voice work (voice-brief.md), the same in every voice, under 100 characters, no em-dash. The first sentence of each is the first brief's decided sentence where one exists; the second mirrors its offline line ("Offline. Undo, history, apps and panels still work.") and claims only what is true: the browser needs the network and an app's ask button waits, so "everything still works" would overclaim.

| Moment | Line above the pill | One button | Empty field |
|---|---|---|---|
| Claude warns | Claude 5-hour limit at 84%, resets 15:00. (dev-brief.md) | Use Codex until 15:00, if Codex is signed in | unchanged |
| Cut off partway | Claude hit its limit partway, after changing 2 files. It carries on at 15:00. | Undo · Details (today's, only if it changed something) | |
| 5-hour limit | Claude is at its limit until 15:00. Your apps and files still work. | Use Codex until 15:00, if Codex is signed in | Open or find anything. Asks wait for 15:00. |
| Weekly limit | Claude is at its weekly limit until Thursday 09:00. Your apps and files still work. | Use Codex until Thursday 09:00, if signed in | Open or find anything. Asks wait for Thursday 09:00. |
| Spending limit, time known | Claude is at its spending limit until 15:00. Your apps and files still work. | Raise the limit | as the 5-hour limit |
| Spending limit or credits used up, no time | Claude is at its spending limit. Your apps and files still work. | Raise the limit, then Try again | Open or find anything. Asks wait for Claude. |
| Codex | The same lines with "Codex" (time from its text) | Use Claude until 15:45, if signed in | the same |
| Opus-only weekly limit | Opus is at its limit until Thursday 09:00, so Sonnet answers until then. | none | unchanged (no rest) |
| On usage credits | Claude is using your usage credits until 15:00. | none | unchanged (no rest) |
| By hand | Claude is paused. Your apps and files still work. | Resume Claude | Open or find anything. Asks wait until you resume Claude. (Waiting chips read "paused".) |
| A sentence while resting | Kept for 15:00. Found on this computer: (or "Nothing on this computer matches.") | the found chips | |
| Back | Claude is back. Running your 3 waiting asks. (or "Claude is back."; fades after 8 s) | none | Ask anything |

Times read "15:00" for today, a weekday ("Thursday 09:00") within six days, and a date ("1 Nov") beyond that; never "tomorrow", so the voice brief's rule on promising words holds. The name of whose limit it is comes from the provider's message when it says so ("your org's", "your team's"); otherwise the line stays neutral.

**What you can still do:** every launcher word (apps, browser, terminal, files, undo, history, stop, hide, desk, lock, restart, shut down, wifi, sound, brightness, battery, sign in, "use claude"/"use codex", the widget words, the picture words such as "network" and "disks"), "!" commands, the Details drawer, your own words once words.toml lands, Brain search once it merges, and finding. Anything else waits as a chip.

**How it should look** (the mark's design draws it; this is what it should mean):
- **Meaning:** the machine is here and your things work; its AI is resting until a time or until you wake it.
- **A proposal:** a still stone drawn as one whole, unbroken outline in muted grey, with the b inside as a visible grey line (in Stone.qml the b is drawn in the ground colour to read as a cut, which would vanish on a hollow stone). The pill's border stays neutral, not offline's red.
- **Colour, by elimination:** not red (nothing failed), not amber (it is not your turn), not orange (nothing is acting), not green (the AI cannot answer). Grey already means "not acting" for Stopped's square.
- **Without colour:** hollow and whole, against rest (filled), offline (broken outline) and stopped (square). At 24 px a whole outline and a broken one may be hard to tell apart, so a faint fill may be needed; the mark's design decides.
- **Motion:** none, so the closed list of movements stays closed (identity-brief.md). Coming back simply fills green again.
- **One look** for a hand pause and a limit.
- **Code:** a new `face` value, `resting`, in PillState.qml (:73) and a drawing in Stone.qml, both the mark's code, on main; it must never fall into the `needs` branch.

## What keeps working

| Feature | While the AI rests | Piece that owns it |
|---|---|---|
| Launcher words, picture words, "!" commands | Work at once; matched before anything is queued (agentd.py:283) | this design adds pause and resume |
| Undo, history, Details, Stop | Work; undo still covers system files at the next restart, home and apps stay (launcher.py:463) | |
| Apps you made and their processes | Work; each is its own process | The app kit |
| App buttons that ask the agent | Show "At 15:00"; a press waits as a chip; the answer arrives after the reset | The app kit (AskButton) |
| Browser panel and its sign-ins | Work in Bombadil's own profile; pages need the network | |
| Terminal, Files | Work | |
| Background jobs | Keep running; Stop, dismiss and Why? work; "Run again" comes in piece 7 | The desk |
| Desk: Watching, Needs you | Work | The desk; coding sessions |
| Desk: Now | Empty, since no turn runs | The desk |
| Desk: Away, Machine, Alive | Designed from local sources, not built yet (DeskState.qml models are null) | The desk |
| Pictures of the machine | Work, no model (sysmap) | Why lines and pictures of the machine |
| The agent's own why lines and cards | None, they are its words | Why lines and pictures of the machine |
| Brain search, Focus, "why is this here?" | Work (local SQLite FTS5); new one-line descriptions wait, old ones stay greyed | The Brain |
| Your words (words.toml), Noticed counting | Work; accepting an offer waits, until piece 7 writes it locally | Self-improvement (the Noticed chip and self-checks) |
| Coding sessions | Claude and Codex sessions read "Paused until 15:00" on their own dots; shell sessions work | Coding sessions |
| Mail view, message cards | Reading, archiving, your own reply and Send work; "Needs a reply" marks and drafts from your words wait | Mail |
| Greetings | Local templates; the limit line outranks them | Voice |
| Updates | "update" is a shell step and runs; the steps that need the agent (Arch news, .pacnew merges) wait and say why (passenger-brief.md) | The installed OS |
| Install, Repair's "Go back", the bad-start rollback | Work; no model on these paths (installed-os-brief.md). Repair's "Fix it" is a turn and waits | The installed OS |
| "Talk to Claude here" on the rescue screen | Shows the resting line instead of failing | The installed OS |

## What happens to

**A turn cut off partway.** What it did stays: files, packages, a half-made app, jobs it started (which keep running). If it changed something, its closing line keeps Undo and Details, which say what they cover; this brief promises no more than that. The ask goes back to the front of the chips and, at the reset, carries on in the same conversation with a note: "[The last try stopped at a usage limit after: wrote tracker/main.qml, tracker/app.toml. Check what is done before redoing it.]" Whether the CLI kept the half-turn in the conversation is unverified, so the note covers either case.

**Asks already waiting.** They stay as chips with the time and are not sent. At the reset they run in the order you typed them; × removes any of them before then. The chip showed the time the whole while, so running then is what it promised.

**Asks typed while resting.** Launcher words and "!" commands run at once. Anything else becomes a chip and gets its found chips. "On it" never shows for it.

**Apps whose buttons ask the agent.** The ask waits as a chip like a typed one, with the app's name, at most one per app; the answer reaches the app through `replied` after the reset. An app never learns about a limit through an error string.

**The machine's own background model use.** On main there is none today except apps' asks, which follow the rule above. On the branches and in the designs: Brain descriptions, the Noticed loop's daily call and menders, routines that ask the AI, mail's "Needs a reply" marks, broken-app self-fixes, fresh-session seeds. Each reads rest.json and waits; none counts the rest as a failure; none becomes a chip, because none is your ask. One risk is for the Brain's work: if its `claude -p --output-format text` exits 0 at a limit (unverified), the limit sentence could be saved as a file's description.

**Coding sessions.** They are the vendors' own programs on the same account, so a limit reaches them on their own: their dots read "Paused until 15:00" from rest.json, Claude's own resume at the reset stays on (dev-brief.md), and a limit never makes a session your turn. A hand pause leaves them alone: you opened them, and you stop them by name. (Today the unmerged coding-sessions work marks a session that hits a limit as failed and red, dev.py:576-579 there; that work changes it.)

**The browser.** Unchanged: its own profile, every sign-in kept, links from anywhere still open in it. The agent's hands in the browser stop only because no turn runs.

**Routines and promises.** Promises and watchers turned into plain checks run with no model (passenger-brief.md). A routine that asks the AI and falls due while resting runs once after the reset, not once per missed time, and a rest never counts toward "two failed runs in a row disable it".

**`bombadil ask` in a terminal.** It prints the resting line once and keeps waiting with its ask held, instead of blocking silently; Ctrl+C drops the ask.

## A day with it

| Time | What you see | Behind it |
|---|---|---|
| 10:40 | "Claude 5-hour limit at 84%, resets 15:00." once after a turn | `rate_limit_event` with `allowed_warning` (piece 3) |
| 11:52 | The stone goes hollow. "Claude hit its limit partway, after changing 2 files. It carries on at 15:00." with Undo and Details | A refused turn: 429, `rate_limit`, an event with `rejected` and `resetsAt` |
| 11:53 | "Claude is at its limit until 15:00. Your apps and files still work." The field: "Open or find anything. Asks wait for 15:00." | The resting line |
| 12:05 | "passwords" opens Passwords. The browser is still signed in. The ISO is at 61% | Launcher words, the browser's own profile, the jobs table |
| 12:30 | "make the tracker blue" becomes a chip labelled 15:00 | The queue gate that already exists |
| 13:10 | In Notes, Summarise shows "At 15:00"; a press adds "from Notes: summarise this" | `Agent.ready` and `note` |
| 13:20 | "the march invoice": "Kept for 15:00. Found on this computer:" and three chips | The finder (piece 2) |
| 15:01 | The stone fills green. "Claude is back. Running your 3 waiting asks." The tracker carries on first | The wall clock passes `until`; the first waiting ask is the check |
| Sunday 20:00 | Click the stone, flip Claude off: "Claude is paused. Your apps and files still work." | The AI card's switch, the same state |

## Questions, and the answers

**Confirmed by the owner on 1 October 2026: options 1a, 2a and 3a, the recommended option each time.** The three decisions are recorded just below; the options stay as they were asked.

**Decided**
1. **The hand switch is a click on the stone** when nothing runs, opening a small card with one row and one switch per AI, plus the words "pause Claude" and "resume Claude". No switch on the bar.
2. **No local AI for now.** Nothing about a small model is built, installed or packaged; piece 9 stays on the list only as a record of what was weighed and is reopened only if that answer changes.
3. **Recipes are in.** When Claude fixes something that will likely be asked again, it may keep the exact commands under a name; each run saves a restore point, and only tasks whose every step undo can take back are kept. This is part of piece 7.

**What the answers change in the build:** piece 1 includes the AI card and the two words; piece 2 and the finder never call a model; piece 7 includes recipes, next to the Noticed chip's local word write; piece 9 and the packages for a local model are dropped from every other piece's list.

Each of these changes what you see or what the machine does for you. The questions as asked:

**1. The owner asked for a toggle. Bombadil flips it by itself when Claude runs out, so where should the switch be for the times they want to flip it themselves?**
- a) Click the stone when nothing is running. A small card shows each AI, whether it is ready or until when it rests, with a switch to pause it. Typing "pause Claude" and "resume Claude" works too. **Recommended:** nothing new sits on screen at rest, and the switch is where the machine already shows its state.
- b) The same, plus a switch always visible on the bar.
- c) Only the words "pause Claude" and "resume Claude", with no switch anywhere.

**2. Should Bombadil keep a small AI on the computer itself, for when Claude is out?**
- a) Not now. Everything you made keeps working, and the pill finds your apps, files and past asks without any AI. **Recommended:** on a 5 GB test VM it would take a fifth to a half of the memory, answer in seconds, and still pick the wrong thing about one time in ten.
- b) Yes, as a download you turn on, on computers with 16 GB of memory or more. It only suggests which app, word or file you meant, and never acts.
- c) Yes, a bigger one that can do small jobs by itself, on computers that can run it.

**3. Should Claude be able to leave you named shortcuts that run commands, so they still work when it is out?** Today the words Bombadil learns can only open or show things.
- a) Yes. When Claude fixes something you will likely ask again, like "fix the sound", it keeps the exact commands under that name, and each run saves a restore point. Only shortcuts that undo can fully take back are kept. **Recommended:** it turns what the machine learned while it had a plan into things you can still do without one, and the undo rule keeps it safe.
- b) Only a "Run again" button on finished jobs in the Watching card.
- c) No. Learned words keep only opening and showing things.

## Decisions I picked a default for

**Does the switch flip by itself?** Default: yes, on the first refused ask, with the narrow conditions under piece 1. This keeps the starting default and tightens it: the refusal decides, the event only says which limit and until when. A switch you had to notice and flip would leave today's failing queue in place. Other options: by hand only; on the event alone (it can be stale, repeated, or "rejected" while credits pay).

**Can it be set by hand?** Default: yes, into the same state, with the card's switch and two words (question 1). Real reasons to want it: keeping the rest of a week for coding sessions, which drain the same plan (dev-brief.md); a spending limit you can see coming; a demo; and trying every line of this brief on the VM for free. Other options: a switch on the bar; no hand switch.

**What does "still use the OS" mean?** Default: the starting list (apps and their processes, the browser with its sign-ins, files, the desk widgets that read the machine, and the pill as a launcher), with three corrections. Most of it is already true today; what is missing is that the machine knows it is out, and the apps that ask the agent. The pill is more than a plain launcher: it still takes any ask as a chip and answers a sentence with things it finds (piece 2), because a launcher that knows only exact words leaves every sentence unanswered. And of the desk widgets only Watching and Needs you have a data source today; the browser needs the network; a job that ended needs the agent to start again until piece 7. Other options: a plain launcher only; a separate "poor man" screen.

**Hold asks, or refuse them?** Default: hold them as chips labelled with the time, typed and from apps alike. The pill already holds asks in every state that is not ready, and refusing would make you type them again. Other option: refuse with a line.

**What runs at the reset?** Default: the cut-off ask first, in the same conversation, then every waiting ask in the order typed, with no age rule. The chip showed the time the whole while and × removes any of them; a hidden rule would need explaining. Other options: asks older than 5 hours wait for a tap (two drafts proposed it for weekly limits); nothing runs without a tap.

**One button or several?** Default: one per case, as the first brief decided (ux-brief.md). "Resume Claude" on a timed limit would only be refused again, and "Raise the limit" appears only on spending limits, so time limits never nudge you toward spending. Undo and Details stay where they already are, on the cut-off closing line.

**Use Codex until 15:00.** Default: one button, only when Codex is signed in, never by itself. This is the first brief's decision (ux-brief.md, confirmed 2026-09-27), so it is not asked again. Moving drops the conversation and spends a second account. Other option: move by itself.

**Opus to Sonnet by itself.** Default: yes, when only one family's weekly limit is hit, said once. Free, keeps the conversation, "use opus" undoes it; it waits for the model setting of the unmerged VM fixes. Other option: offer it as a button.

**Who may spend paid usage credits?** Default: what you ask for (typed asks and app asks) may; the machine's own background use waits for the reset. Claude Code keeps going on credits by itself once you have turned them on at claude.ai; Bombadil adds only the restraint on its own background calls and one line saying credits are in use. Other options: everything; nothing past the plan's limit without a press.

**Does it check whether the limit is over?** Default: no test calls. With a time, the first waiting ask after it is the check, and if nothing waits nothing is sent; with no time, Raise the limit or Try again. Claude's interactive tool probes with a real one-token call (from the binary); Bombadil does not need one. Other options: an hourly small-model check, which spends and depends on the Brain branch's command; `claude -p "/usage"` once checked (piece 8).

**The refused turn's restore point.** Default: kept, but the undo marker goes back so the next "undo" skips it if the turn changed nothing. Since only one turn is refused per window now, that is enough, and it needs no new sudo operation. Other option: delete the empty restore point.

**Is resting ever your turn?** Default: no. No amber knock, no desk card (widgets-brief.md, dev-brief.md, identity-brief.md), even for a spending limit where you have to act: Raise the limit is a button on the line. The desk's design owns when needs-you shows, and today's setup states that wait on a person (choose, signed out, offline) do show it; resting is different because there is nothing you must do.

**No ring on the stone.** Default: the warning line and the card's number instead of the passenger brief's fuel ring (passenger-brief.md). The identity brief's list of shapes has no ring and its rule says "never a ring" for needs-you; an amber ring would read as needs-you. The mark's design confirms.

**A hand pause survives a restart, and leaves coding sessions alone.** Default: yes to both, and the line says "Claude is paused." at every start. Other options: clear it at restart; pause sessions too.

**Offline and outages.** Default: later, in the same shape (piece 5), keeping offline's own line. Not in piece 1, because `_wait_online` starts a sign-in when the network returns, which must change first.

## Owned by other pieces of work

| Piece | What it owns here |
|---|---|
| [The mark and the design system](identity-brief.md) | Draws the resting stone (a new `face` value and its drawing in Stone.qml, on main); settles the passenger brief's fuel ring against "never a ring" |
| [Voice](voice-brief.md) | Words every line, button and placeholder above; confirms the greeting is dropped under them and the empty field carries the state |
| [The desk](../architecture/desk.md) | Confirms resting is not needs-you and stays off the desk; Watching's "Run again" (piece 7); later, Away lists what ran at the reset |
| [The app kit](../architecture/app-kit.md) | AskButton, the skill's rule "a button that asks the agent is an AskButton", the paragraph in `share/skills/bombadil-apps/references/native.md`, and the `[[does]]` rows with the `--do` entry point (piece 6); piece 1 changes agent.py itself |
| [Why lines and pictures of the machine](passenger-brief.md) | The holding card the AI card grows into (passenger-brief.md), routines that wait out a limit without counting failures, and the parse its brief planned (passenger-brief.md), which piece 1 now builds |
| [Coding sessions](dev-brief.md) | "Paused until 15:00" from rest.json instead of red; its 80% line is the same line as piece 3's |
| [Self-improvement](self-improvement-brief.md) (the Noticed chip and self-checks) | A rest is never a finding; refine, group naming and menders read rest.json; the local words.toml write (piece 7); recipes (question 3 a; it changes self-improvement-brief.md) |
| [The Brain](brain-brief.md) (index and Focus view) | Descriptions wait while resting and never save a limit sentence; FTS5 search joins the finder |
| [Mail](everyday-work-brief.md) (the Mail view and message cards) | "Needs a reply" and drafts wait; reading, your own replies and Send keep working |
| [The installed OS](installed-os-brief.md) | The rescue screen's "talk to Claude here" shows the resting line; the session id saved across reboots; no local model packages (question 2 a) |
| VM fixes (not on `main`) | The session id saved across agentd restarts, "use sonnet" for piece 4, the 30-point restore limit that resting now protects, and the agentd limit lines it may touch next |
| Testing on the VM | The 429 script in scripts/vm-tools/scratch_api.py, the "pause Claude" walk-through, and capturing a real limit on a long-lived test VM as a fixture |
| [Signing in from the pill](../architecture/browser-and-signin.md) (resolved) | Built the setup states that `resting` joins; the sign-in guard changes under piece 1 are part of this build |

Pieces 1 and 2 need work of their own; the design touches only main's agentd, providers, launcher, the kit's agent.py and the shell.

## Extends or changes the earlier briefs

- **ux-brief.md.** "Everything else goes to the agent" gains an exception: while the AI rests, everything else waits as a chip with things found on the computer.
- **ux-brief.md.** The limit line keeps its first sentence and gains a second; "[Use Codex until then]" becomes "Use Codex until 15:00", one button per case as decided.
- **dev-brief.md.** The 80% line is shown when Claude warns, from the CLI's own numbers; the pill's own conversation gets the sessions' "until 15:00" shape, and the sessions read rest.json.
- **passenger-brief.md.** No ring on the stone; the line and the card's number take its place. The rate-limit parse it planned is built in piece 1. The click on the stone at rest opens the AI card, the first row of the holding card.
- **passenger-brief.md.** The "when online" chip and the time chip become the queue's one grey chip with three labels: "next", a time, and "when online".
- **passenger-brief.md.** The local model stays out for now (question 2); if it comes, it is reached from the resting line too.
- **passenger-brief.md.** A rest is never a failed routine run.
- **passenger-brief.md.** "pause <name>" gains the AIs as names.
- **voice-brief.md.** The order of lines is unchanged; `resetsAt` or the provider's own text is the registry that backs "until 15:00"; with no time, the line names none.
- **identity-brief.md.** A new still state with no new motion or colour.
- **self-improvement-brief.md.** Making a word may be written locally (piece 7); recipes (question 3 a); a rest is not a finding; rest.json is the menders' quota input.
- **widgets-brief.md.** Unchanged: the limit stays in the pill, and there is no counter at rest.

## Cut, and why

- **A small AI installed on every computer.** Question 2. It costs gigabytes and memory every day for a feature most days never use (passenger-brief.md), and on the 5 GB VM nothing above about 2B parameters fits beside the desktop.
- **Claude Code or Codex pointed at a local model** (Ollama, llama.cpp, LM Studio). It works technically, but Claude Code's own prompt is 14 to 33 thousand tokens, which means minutes per turn on a CPU; Anthropic does not support that route; and a small model would drive a root shell in full-auto, where it loops and reports success it did not achieve.
- **A parser that guesses and acts.** Two brains that sometimes disagree (ux-brief.md). The finder shows; you press.
- **Replaying a past turn's commands blindly.** They depended on what the turn found halfway; recipes the AI chose to keep replace this.
- **Embeddings for matching meaning, for now.** They reopen the Brain's recorded cut (brain-brief.md); app "does" rows cover most paraphrases for nothing.
- **A fuel ring, a token counter, a limit card on the desk.** widgets-brief.md and identity-brief.md; "never a ring".
- **Fixed 80% and 95% lines.** The public field is set only when Claude warns; the per-window numbers are marked internal.
- **Test calls to see whether the limit is over**, including an hourly small-model check. The reset time is known, and a test call is still a call.
- **`--fallback-model`.** It skips 429 errors by design (from the binary), so it never fires on a limit.
- **Undocumented usage endpoints** (`/api/oauth/usage`, `chatgpt.com/backend-api/wham/usage`).
- **Buying credits from the pill.** Money cannot be undone; Raise the limit opens the provider's page.
- **A mode name on screen** ("Low power", "AI off", "AI paused"). One more thing to learn; the plain sentence names the AI.
- **Greyed-out, disabled ask buttons in apps.** They refuse an ask; the AskButton keeps it.

## Checked today

**Read on main at 26843d3** (after the app kit, the shared theme, the stone in the pill and the pictures merged):
- agentd.py: the limit constants (:139-143); `_status` (:356); `_setup_msg` and `_describe`, whose "Use … instead" chips show only when signed out (:866-911); `_set_access` waking the worker on ready (:913-920); `check_access` saying ready whenever credentials exist (:922-945); `signin_asked` guarding only `ready` (:947-955); `_wait_online` calling `start_signin` (:988-999); `_signed_out_turn` re-queueing at the front and restoring notes (:1143-1160); `_runnable` (:1164-1166); the turn start: turn count, `clear_undo`, restore point (:1241-1254); the watchdog line (:1352); `_on_event`'s failure handling, the session id adopted only from a turn that worked, and the drop at `num_turns == 0` (:1455-1470); `_limit_text` (:1499).
- providers.py: `is_progress` ignoring `api_retry` (:234-237); the assistant branch reading only `authentication_failed` (:282); the result mapping (:299); Codex's `turn.failed` and top-level `error` (:474-481). Nothing on main handles `rate_limit_event`.
- shell/PillState.qml: `setupState` and `ready` (:24, :28); the new `face` and its `needs` branch for choose, signed_out and offline (:61-80); `turn_end` preferring the red error (:196-216). shell/shell.qml: the border (:360), the stone and its TapHandler enabled only while stoppable (:379-397), the placeholder (:410). shell/QueueChips.qml: the "next" label (:32). Stone.qml exists.
- launcher.py: the word tables (:37-58), the picture words (:63-79), the undo line about home and apps (:463).
- appkit/native/agent.py: `handle` reads only `busy` and `provider` from status (:113-118); errors are appended to `reply` (:109-111); asks are prefixed "[from app …]" (:161); four properties (:167-170).
- No holding card exists on main.

**From the research sweeps, checked in the Claude Code 2.1.286 binary and the Codex source (main 60947e2, same as rust-v0.157.1):**
- `rate_limit_event` fields (status allowed / allowed_warning / rejected; `resetsAt` in epoch seconds; `rateLimitType`; `utilization` 0 to 1, set only with a warning; overage fields; `unifiedWindows` marked internal); it is sent on a change, and a rejected one again every 30 seconds.
- The assistant `error` enum includes `rate_limit`, `billing_error`, `overloaded`, `server_error`; a refused turn ends with `is_error: true`, `api_error_status: 429`, `terminal_reason: "api_error"`, and text such as "You've hit your session limit · resets 3:45pm". The old "Claude AI usage limit reached|<epoch>" format is gone.
- Window names: five_hour "session limit", seven_day "weekly limit", seven_day_opus "Opus limit", seven_day_sonnet "Sonnet limit", overage "usage credit limit".
- `--fallback-model` skips 429. The event exists only for claude.ai logins.
- Codex exec carries no rate-limit numbers; its limit message uses U+2019, local time as "3:45 PM" or "Oct 2nd, 2026 3:45 PM", and exec exits 1; "rate limit exceeded:" is a retried throttle.
- Raise-the-limit pages: claude.ai/settings/usage, claude.ai/admin-settings/usage, chatgpt.com/codex/settings/usage.
- Local models: Arch's ollama (58 MB installed) and llama-cpp (17 MB, with a user unit); small models' tool-picking from a public CPU benchmark; the VM's memory (scripts/wsl-vm.sh, 5G; run-vm.sh, 6G). Much of the speed data is secondary.

**Not run:** no model turn, no real limit captured, no Codex binary, no local model.

## To check before building

1. Capture a real Claude limit in `-p` stream-json on a claude.ai login: the order of `rate_limit_event`, the assistant error and the result; whether a 5-hour limit is retried first; the `num_turns` of the result; whether the refused exchange is written into the resumed conversation; whether a cached event arrives before `system/init`.
2. Whether a refused request is billed (a 429 carries no usage, but no billing document says so).
3. What the event shows while paid credits carry on, and whether spending limits come with `resetsAt` or `overageResetsAt`.
4. Codex's real output at a limit on the version Bombadil installs.
5. Whether `claude -p "/usage"` works without a terminal and with no model call (piece 8).
6. The exit code of the Brain's `claude -p --output-format text` at a limit.
7. Whether the coding sessions' `StopFailure` hook carries the limit text.
8. Which text the CLI prints for the fake API's 429 under the API-key login: the subscriber wording or "API Error: Request rejected (429)".
9. That every line fits 100 characters with a 24-character name and no em-dash (voice-brief.md), and whether "Thursday 09:00" or "Thu 09:00" reads better.
10. That a whole grey outline and offline's broken red one read apart at 24 px without colour.

## The page

The page is [`pages/the-poor-man-switch.html`](pages/the-poor-man-switch.html) in this folder: a case switcher for the lines, the stone states at real size, and the three questions with their recorded answers. Open it in a browser.

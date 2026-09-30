# Bombadil's voice

You sign in to Claude and the browser slides away. A small card rises above the pill with one field that says "Your name" and three rows under it: Merry, Plain and Quiet. Each row shows how it would welcome you. You type Daniel, and the rows rewrite themselves as you type. Merry reads "Welcome, Daniel. Let's go for a walk!", Plain reads "Welcome, Daniel.", and Quiet says it will not speak on an ordinary morning. Merry is already highlighted, so you press Enter. The card folds, the Merry line takes its place above the pill for a few seconds, and the ten-second tour and the example chips carry on exactly as decided. That was the whole setup. About ten seconds, no settings page, no model call.

The next morning you boot and see wallpaper and a pill. A second later one line rises above the pill: "Good morning, Daniel. Where to today?" You touch the keyboard, and eight seconds later the line is gone. After lunch, "While you were away: builder finished (4 files), the ISO download is done." After a week, "Welcome back, Daniel. Since last time you built Passwords and Tracker, and 214 updates are waiting." If you close every window, nothing appears at all, because a screen at rest is wallpaper and a pill. In an empty folder in the Brain you read "Receipts is empty. Drop files in, or ask for what belongs here." with one chip that does it. If you type "call me Dan" or "be less chatty", the next reply, and the next greeting, already use the change.

This is the sixth design pass. Daniel asked for a widget in the one-time setup that gives Bombadil a voice and asks what to call him, kept simple, with recommendations and options; and for a format for what the desktop shows when it boots with few windows open, or when a place is empty, for example "Welcome [Daniel], Let's go for a walk!". It sits on the five briefs before it (How Bombadil should feel, Building on Bombadil, Bombadil's Brain, The Bombadil Desk and Riding with Bombadil), whose decisions stay in force. The desk design was accepted on 28 Sep, and Riding with Bombadil hands the words of the welcome line and of empty states to this brief. Four independent drafts were written from different angles (the smallest thing, a real character, what the code allows, the passenger's whole day), seven of the sixteen planned critiques came back before the workflow was cut short, and I merged the four and applied the critics' blocking findings myself. The "Cut" list says what that took out.

By "voice" I mean how Bombadil writes: the words of its greetings, goodbyes and closing sentences. Speaking aloud belongs with the speech service the first brief already plans for a later milestone.

## The rules

1. **Bombadil speaks when you arrive or leave, and is silent at rest.** A greeting is an event that fades by itself, never a fixture. The rest screen stays wallpaper and a pill, exactly as decided, and closing the last window shows nothing.
2. **Every line is true.** It is built from what the machine knows (the clock, the turn log, the registries), a fact is said once, and a line with nothing true to say is silence. The one generic part of any line is a short invitation, and only Merry has one.
3. **The voice changes words, never facts.** Counts, names, marks and errors read the same in every voice. "On it", the narrated steps, Undo and Details, provider trouble, limits, offline and the put-back lines stay plain in every voice, because people search for them and paste them into bug reports.
4. **One line, never a chat.** A greeting is a hello sentence plus at most one fact sentence, 100 characters at most, so it is one row. A voice may not lengthen the decided limits: one line while working, at most four after.
5. **Nothing starts without you.** A line offers and never runs. A chip under it is a sentence you could have typed, and clicking it is you typing it.
6. **Changing it is a sentence, not a page.** "Call me Dan", "be less chatty" and the one word "voice" are the whole settings interface.
7. **It costs nothing at boot.** Greetings and empty-place lines are local templates. They are instant, free and work offline, which matters because boot is when the network is least likely to be up. A model writes only its own closing sentence, inside a turn you asked for.
8. **An empty place says what is empty and gives one way to fill it.** It names the place, says why or what fills it, offers one real doorway, and never says "Oops" or "Nothing here yet" on its own.

## Build these first

Two pieces. The first gives Bombadil a name for you and a voice, and puts Daniel's own line on the screen at boot. The second makes the line know things and gives every empty place words. Both edit PillState.qml and shell.qml, so they follow the merge of PR #5 (the setup channel and the installer's copy of ~/.config/bombadil) and rebase over PR #4's placeholder change.

### 1. Bombadil says hello

**What you see:** After sign-in a small card rises above the pill under one line, "Signed in to Claude. What should I call you?" It holds one name field and three voice rows, Merry, Plain and Quiet. Each row shows its own welcome and its own closing sentence with your typed name already in them, so you choose a voice by reading your own name said three ways. Up and Down move the highlight, a click picks a row, Enter accepts, and Esc skips with the defaults (Merry, no name). The card folds and the chosen voice's first line takes its place: "Welcome, Daniel. Let's go for a walk!" From then on every boot begins with one line in that voice, every reply from either provider sounds the same way, "call me Dan" renames you, "be less chatty" moves you to Plain, and the word "voice" brings the card back with your choices filled in.

**Why first:** It is the widget Daniel asked for and the line he wrote. A voice needs a name to say and a place to say it, and everything else in this brief (facts, returns, empty places) only fills that line with more true things.

**What changes:**
- A new `src/bombadil/persona.py` reads and writes `~/.config/bombadil/persona.toml` with three keys: `name` (optional), `voice` (merry, plain or quiet) and `greet` (true by default, false after "stop greeting me"). It is read defensively: a broken file, an unknown voice or a name that fails the check falls back to defaults, so it can never stop agentd from starting. A name is one to three words, at most 24 characters, letters, marks, spaces, hyphens, apostrophes and dots only, and may not start with `!` or `/`. It is a separate file because `config.save_user` rewrites all of config.toml with only the provider and model (config.py:43), and because memory.md is loaded by every coding session as their CLAUDE.md and AGENTS.md, where a merry voice would leak into code reviews.
- The voice reaches the model as one sentence of 22 to 42 words appended to the system prompt. `providers.py` gets a `system_prompt()` that returns SYSTEM_PROMPT plus `persona.note()`, used by the two places SYSTEM_PROMPT already goes (Claude's `--append-system-prompt` at providers.py:103, Codex's `-c developer_instructions` at :182). The three sentences are under "The voices" below. It applies to every fresh session, and a change made inside a running session lives in that session's own history, because the request is in it. There is no per-turn prefix: about 90 words added to every turn would pile up in a resumed session, and the brain brief decided against adding anything per turn beyond the "this" line. Coding sessions never get it.
- agentd: when sign-in reports ready and `persona.toml` does not exist, it writes the defaults at once (so any way out of the card leaves a valid setting and the card is never asked again), shows the line "Signed in to Claude. What should I call you?" in place of the ready line for that first sign-in only, and sends a `persona_ask` message carrying the three voices with their sample templates. The shell answers with `persona {name, voice}` or `persona_skip`; agentd saves, and broadcasts a `persona` event. A prompt typed or sent from the pill while the card is up simply runs, and the card folds with what is on screen. The card is not an access state, so nothing queues behind it.
- The shell: a new `PersonaCard.qml` joins the bar's column and input mask the way SetupChips does, and takes the keyboard through the non-toggling path PR #7 added (`summonHere`), because the Super-tap `summon()` toggles and would give the keyboard back. The name field is the card's own, so a name can never be mistaken for an ask: the pill keeps meaning "ask" everywhere. PillState gets a `welcome` line mode: it fades 8 seconds after the first key press or pointer movement that follows it (15 seconds for the first hello), never later than 30 seconds after it appears, hover holds it, and typing, Enter, Esc or a turn starting replaces it. StatusLine's fade rule, which today covers only `closing` and `local` (StatusLine.qml:49), gets the new mode.
- The boot greeting: the bar sends one `bar` message when it connects, because agentd sends only status and entries on connect and cannot tell the bar from a `bombadil ask` client. agentd answers it with one `welcome` message per login. The marker is `$XDG_RUNTIME_DIR/bombadil/greeted`, written only after a bar received the line, so restarting agentd (which Daniel does while developing Bombadil on Bombadil) never replays it, and a greeting that was dropped is not counted. In this piece the only facts are the clock, the persona and the turn log.
- The launcher gets the word `voice`: exact, local, offline, and it reopens the card with the current name and voice filled in. It is the only new word in this brief, and it is how a person finds the choice again without guessing a voice name.
- `narrate.py` gives an edit of persona.toml a plain step ("Changed how I talk to you") with no Undo, so "call me Dan" does not end in a sticky receipt reading "Edited persona.toml". Today Undo does not restore /home files anyway, and once /home has its snapper config the file is not among the decided exclusions, so a later Undo will.
- Tests: persona unit tests (the name check, defaults on a broken file, reload on a change of modification time), a card test offscreen in the style of test_pill_qml.py (the samples rewrite as the name is typed, Enter, Esc, Up and Down), the agentd setup step and the skip paths, both providers' `command()` carrying the sentence on a fresh and a resumed turn, and a table test over every moment and voice in this brief: every line fits 100 characters with a 24-character name, reads well with no name, has no leftover braces, no em-dash, and an exclamation mark only in Merry.

**Before building:** the three VM checks at the end of "Checked today", in particular whether a resumed Claude session honours a changed appended prompt, and whether the card really takes the keyboard when the browser panel leaves.

**Effort:** S to M, about two days with the tests.

### 2. What it knows, and every empty place

**What you see:** The boot line starts to carry news: "Good morning, Daniel. 214 updates are waiting, 4 of them security fixes." After a break, one line says what happened while you were away, in the dev brief's own words. When you shut down, the line says "Good night, Daniel. batch stops at 14 of 20 and waits for you." Every place that can be empty (a folder in Focus, the Map, the history, "what's running?", an app with no data) says what is true and offers one real next step. Closing every window still shows nothing.

**Why second:** It needs sources that land in other threads (the registries, the update count, an away signal), and each is one small function that returns a clause or nothing, so the line improves as they land and a missing one is simply absent.

**What changes:**
- A new `src/bombadil/greet.py`, pure: `build(persona, now, away, sources)` returns the line, its fade and whether to show it. It holds the grammar, the ranking, the ledger and each voice's templates in one shipped table, `share/voice/lines.toml` (installed at /usr/share/bombadil like the app template). Variants are chosen by the number of days since setup, never at random.
- Sources, each a function returning a clause or nothing: turns.jsonl, the dev registry (PR #4), the jobs registry and promises (the desk's Watching and the passenger brief's piece 4), `/var/lib/bombadil/updates.json` (passenger brief piece 6, including its security count, with no network call at boot), and the machine's own put-back records.
- turns.jsonl rows gain `made` (the app names the narrator already knows from its create_app steps) and `changed` (a boolean), and agentd appends one `{kind: "boot"}` row when a greeting is sent. "Since last time" is then the rows after the previous boot row. Without that row there is no anchor, because the runtime-dir marker is wiped at every login.
- The ledger: a small `~/.local/state/bombadil/said.json` stores each fact with its value and the day it was said. A fact is not said again unless its value changed, or seven days passed for a standing count such as updates. A fact that already has its own place on screen (the your-turn line in the placeholder, a dot's text) is left out of the greeting.
- Away: the return greetings need a signal that nothing produces today. The dev brief's presence file is an existence marker, and hypridle is in no package list. The shell can own it: a Quickshell `IdleMonitor { timeout: 300 }` (Quickshell.Wayland is already imported) writes `{type: "presence", idle: bool}` to agentd, and agentd stamps `last_active` on every prompt, summon, stop and setup action. If the dev thread prefers a hypridle listener, it feeds the same message. Until one exists, only boot and goodbye are live.
- The goodbye is rendered in agentd and sent as the start text of the shutdown. The string the launcher returns at launcher.py:470 is the done text, sent after `systemctl poweroff` has already been queued, so it may never be drawn. Restart keeps its plain "Restarting." and never loses a pending-undo line.
- Empty places: the kit's EmptyState gains one property, `ask`, a chip (a click sends its label as a turn, a label ending in an ellipsis only fills the pill). The apps skill gets one sentence: an empty state names what is empty and gives exactly one way to fill it. The shell-owned empties (Tab on an empty pill, "what's running?", "what are you watching?", "what do you know about me?", history) use the grammar now, and the Brain thread adopts it for Focus, the Map and Time.
- The clock, which is wrong for everyone: bombadil-install:51 forces `/etc/localtime` to UTC inside the installed system after the live root was copied, so a zone chosen on the live USB is overwritten, and nothing in iso/ enables time sync. The installer stops overwriting a chosen zone, systemd-timesyncd is enabled, and on a machine still on UTC the first hello carries one chip, "Set my time zone", which fills the pill with "set my time zone to " and waits for a city. Until a zone is set, openers are "Welcome" and "Welcome back" with no time-of-day word.
- Tests: a table over every moment with a fixed clock and each missing source, the ledger (said once, said again after a change), a golden test that Plain reproduces the earlier briefs' decided sentences word for word, and a PillState test for the welcome mode's yielding rules.

**Effort:** M. The return greetings wait for the away signal and are the last part to go live.

## A day with the voice

**First boot.** The browser slides away after sign-in. The line says "Signed in to Claude. What should I call you?" and the card is under it. You type Daniel and press Enter. "Welcome, Daniel. Let's go for a walk!" holds above the pill while the tour runs, and the three example chips and the faint "Super to talk. Esc to stop. Say undo to go back." follow as decided. On a live USB the Install chip and the note that undo starts after install come first, as decided.

**08:40, the next morning.** One second after the bar is up the line reads "Good morning, Daniel. Where to today?" (or, while the clock is still on UTC, "Welcome, Daniel. Where to today?"). It waits until you touch the keyboard or the mouse, then fades eight seconds later. Plain would say "Good morning, Daniel." and Quiet says nothing.

**After lunch.** You unlock the screen. The line says "While you were away: builder finished (4 files), the ISO download is done." There is no hello and no name, because people do not greet each other after a coffee, and "reviewer is waiting" is left out because the placeholder already says it.

**After a week.** "Welcome back, Daniel. Since last time you built Passwords and Tracker, and 214 updates are waiting." Once the desk's Away card exists, it holds that list, and the line shrinks to its opener and a count so nothing is said twice.

**Nothing new.** You come back from a break and nothing happened. Nothing appears.

**A mistake.** While you were away a change cut the network and the machine put it back. The line is the decided one, "That change cut the network, so I put it back. Redo it?", with no hello and no name, because a failure outranks a greeting.

**Asking.** "Call me Dan." The agent edits the file and the line says "Dan it is." "Be less chatty." "Plain it is." "voice" brings the card back.

**End of day.** You type "shut down" at 22:15. The line, drawn before the power goes, says "Good night, Daniel. batch stops at 14 of 20 and waits for you."

## The voices

Three, because they differ on one axis each and can be judged by reading a sample. Merry and Plain differ in style. Quiet differs in how often it speaks.

**Merry** (the default, because it is the voice in Daniel's own example). Light and brisk, with contractions. Greetings may end in an invitation, at most one exclamation mark per line, and only for good news or an invitation. The agent's closing sentence may carry a short aside built from a fact of that turn, in at most one reply in four, never after an error or a change to the system, and never instead of the receipt. Merry in spirit only: no rhymes, no verse, no song lines. Model sentence: "The user goes by Daniel; use it only when greeting or asking. Sound brisk and friendly. At most one reply in four may end with a short aside built from a fact of this turn, never after an error or a system change."

**Plain.** Short and neutral. It says hello with the time of day and the name, no invitation, one or two plain sentences in a reply, no asides, no sign-off. Its wording is the earlier briefs' decided sentences word for word, and a golden test keeps it so. Model sentence: "The user goes by Daniel; use it only when greeting or asking. Keep replies plain and short: no asides, no sign-off."

**Quiet.** Plain's words, but it speaks less. No hello on an ordinary boot, no invitation, no offers. It still says what failed, what finished while you were away and what needs you, and it greets on the first boot ever and after three days or more. Model sentence: "The user goes by Daniel; use it only when greeting or asking. Say as little as will do: one sentence, no aside, no question at the end."

With no name saved, the first clause of each sentence drops out, and so do the name and its comma in every line.

**What every voice does the same:** the facts, the marks, the working line, "On it", Undo and Details, provider trouble, limits, sign-out, offline and the put-back line. A voice owns exactly three things: greetings and goodbyes, the invitation, and the model's own closing sentence.

## The welcome line

**Grammar.** `OPENER[, NAME]. BODY`, at most one row of 100 characters.

- **Opener, by moment:** "Welcome" on the first boot ever, on a boot with no time zone, and at night in Plain; a time-of-day word ("Good morning", "Good afternoon", "Good evening") on the first boot of a calendar day, only when the time zone is set; "Late one" between 00:00 and 04:59 in Merry, with one aside per boot and no comment on the hour after that; "Welcome back" after 4 hours or overnight, and after 3 days or more; no opener at all after 30 minutes to 4 hours away, only the facts. "Good night" is only ever a goodbye at shutdown, and nothing ever tells anyone to go to bed.
- **Name:** in the opener only. It drops together with its comma when no name is saved, is never a stand-in such as "friend" or "user", and never appears in a fact, an error or a chip.
- **Body:** the top one or two true things, worded the same in every voice, joined with a comma or "and". Ranking: something that failed or was put back and you have not seen; something waiting on you (left out when the placeholder already says it); something that finished; something stopped by a restart; what you made since last time (only after 3 days or more away, because telling you what you did yesterday is filler); updates waiting, with the security count; a promise that came true. With no facts, Merry adds one invitation, Plain adds nothing, Quiet says nothing.
- **Merry's invitations:** a pool of eight, picked from the day count since setup, never at random, so it is testable. Daniel's own line, "Let's go for a walk!", comes on day 0 and every fourth day after; the other seven take the days between in a fixed order, so none of them comes back within nine days: "Where to today?", "Shall we wander somewhere?", "What shall we make?", "Ready when you are.", "What's on the road today?", "Somewhere new today?", "Let's see where this goes."
- **Budget:** if a line does not fit, drop the second fact first, then the name.

**Placement.** The line above the pill, in its own `welcome` mode. It appears about a second after the bar connects and the sign-in check says ready, waits for your first key or pointer movement, and fades eight seconds after that (15 for the first hello), never later than 30 seconds after it appeared. Hover holds it. It is never the pill's placeholder (that slot belongs to "Ask anything" and to the dev branch's your-turn line), never a card and never sticky. It shows on the pill's screen only, and is dropped, not queued, when a full-screen window is in front.

**Priority.** Setup, then a running turn, then an error or limit line, then a your-turn line, then the greeting. The greeting is dropped, not delayed, whenever any of those holds the line, and it never clears the placeholder or the mark on the dot. A cheerful "Good morning, Daniel" over an offline or signed-out line would be false.

**When it speaks.** Only arrivals and departures: the first hello, a boot, a return, a shutdown, the first boot after install. Nothing appears mid-day on its own, there is no "want a walk?" nudge, and a restart you asked for (an update, an undo) gets no hello.

**How often.** One boot line per login. A return greeting at most once per 30 minutes, only with something to say after a short break, and never the same fact twice (the ledger).

### The words

Every line below is checked: under 100 characters, no em-dash, an exclamation mark only in Merry. An empty cell means no line.

| Moment | Merry | Plain | Quiet |
|---|---|---|---|
| First hello, right after the card | Welcome, Daniel. Let's go for a walk! | Welcome, Daniel. | Welcome, Daniel. |
| Boot, first of the day, nothing to report, zone set | Good morning, Daniel. Where to today? | Good morning, Daniel. | |
| Boot, nothing to report, zone not set | Welcome, Daniel. Shall we wander somewhere? | Welcome, Daniel. | |
| Boot with updates waiting | Good morning, Daniel. 214 updates are waiting, 4 of them security fixes. | Good morning, Daniel. 214 updates are waiting, 4 of them security fixes. | 214 updates are waiting, 4 of them security fixes. |
| Boot after a restart cut a session off | Welcome, Daniel. batch was stopped by the restart at 14 of 20. | Welcome, Daniel. batch was stopped by the restart at 14 of 20. | batch was stopped by the restart at 14 of 20. |
| Boot at night, zone set | Late one, Daniel. | Welcome, Daniel. | |
| Back after 30 minutes to 4 hours, with news | While you were away: builder finished (4 files), the ISO download is done. | (same) | (same) |
| Back after 4 hours or overnight, with news | Welcome back, Daniel. While you were away: batch finished, 300 done, 20 failed. | (same) | While you were away: batch finished, 300 done, 20 failed. |
| Back after 3 days or more | Welcome back, Daniel. Since last time you built Passwords and Tracker, and 214 updates are waiting. | (same) | (same) |
| Back from any break with nothing new | | | |
| Shut down at night, a session will stop mid-way | Good night, Daniel. batch stops at 14 of 20 and waits for you. | Shutting down. batch stops at 14 of 20. | Shutting down. |
| Shut down by day, nothing at stake | See you, Daniel. | Shutting down. | Shutting down. |
| The machine put a change back | That change cut the network, so I put it back. Redo it? | (same) | (same) |
| First boot of the installed system | Welcome home, Daniel. Undo works from here. | Installed. Undo works from here. | Installed. Undo works from here. |

The facts are the dev brief's and the first brief's own sentences, reused. "batch stops at 14 of 20 and waits for you" describes what the dot does, and promises nothing the code does not do. Lines describe and never promise: a test rejects "will", "tomorrow" and "later" unless a registry backs them.

## Empty places

**Format.** One title-and-line of two short sentences at most: first what is empty, naming the place and never "Nothing here yet" on its own; then why, or what fills it, with a real number or date when there is one. Then exactly one doorway: a chip whose label is the exact sentence it sends (a label ending in an ellipsis fills the pill and waits), or the app's own button. No name, no hello, no "I" where avoidable, no exclamation mark and no voice variants, because empty places teach, agents and docs read them, and an app copied "for my team" should not carry Daniel's tone. The rule that keeps it honest: an empty (nothing yet) is worded, a miss (nothing matches) and an error keep their plain wording, so "The brain is not running yet" stays as it is. It appears only where the empty thing is and leaves the moment the thing is not empty. Only asked-for cards and views get one. A widget with nothing to say is absent, as the desk brief decided, and the Away card does not exist when nothing happened.

| Surface | Line | Doorway |
|---|---|---|
| Every window closed | (nothing) | Nothing is drawn. Super brings the chips back. |
| Focus on an empty folder | Receipts is empty. Drop files in, or ask for what belongs here. | Chip: Find what belongs here |
| Focus on a folder that had one file | Downloads is empty. Its last file, lease-2026.pdf, went into Lease on Tuesday. | The file name opens Focus on it |
| Focus on a thing with no links | No links yet. The Brain has watched since 27 Sep, and nothing else has touched this. | Chip: What is this? |
| Focus search, no match | Nothing called "lease". The Brain only knows what happened since 27 Sep. | Chip: Find lease for me |
| The Map with fewer than three areas | The Map is still small: 2 areas after 3 days. It grows as you save, download and ask. | Chip: files |
| Time or history on day one | Nothing to go back to yet. Undo starts after the first change. (Live USB: Undo starts once Bombadil is installed.) | None |
| Tab on an empty pill, nothing waiting | Nothing needs you. | None (decided line) |
| "what's running?" with nothing running | Nothing is running. | Chip: claude latchkey, the newest folder in ~/Projects |
| "what are you watching?" with nothing | Nothing is being watched. Ask me to keep an eye on something and it shows here with a stop button. | Chip: Tell me if my disk gets nearly full |
| "what do you know about me?", only a name | Only your name so far. Tell me what is worth keeping. | Chip: Remember that... |
| An app with no data | No passwords yet. Add one and it stays on this computer. | The app's Add button |
| "what apps do I have?" with none | No apps yet. Say what you need and I will make one. | Chip: Make me a password manager |

A weekday ("on Tuesday") is used for the last six days and a date after that, because "Tuesday" is wrong after a week. "Since 27 Sep" is the Brain's first-event date, which explains a week-one empty honestly instead of hiding it. A chip that has nothing behind it, for example "claude latchkey" with no folder in ~/Projects, is not shown, because a suggestion the machine cannot back is filler.

## Decisions I picked a default for

Each forks; I picked a default and say why, with the alternatives for the record. The first three are the ones Daniel should look at.

**How should the one-time setup look?** Default: one small card above the pill, after sign-in: a name field and three voice rows with live samples, Enter accepts, about ten seconds. It is the widget he described, the name goes in a field that cannot be mistaken for an ask, and a voice can be judged by a sample, not a name. Other options: questions asked in the pill itself (a typed name can be mistaken for a request, so "install docker" would rename him Install Docker, and a guess at telling them apart is the two-brains parser the first brief rejected); name only, with no voice choice (the voice is what he asked for).

**Where does the welcome show, and for how long?** Default: the line above the pill, waiting for your first touch and fading eight seconds later. It is the one voice already, fades by itself and leaves the rest screen as accepted. Other options: a large hello on the wallpaper for the very first boot only (a memorable moment, but a new element on a screen that is otherwise empty); a card that stays until clicked (a popup by another name, dismissed every morning).

**How far does the voice reach?** Default: greetings, goodbyes and the agent's own closing sentence. The working line, receipts, errors, limits and empty places stay plain in every voice. Other options: greetings only (fully predictable, but the character vanishes the moment you ask for anything); everything including the working line (the line stops being a plain record of what the machine is doing).

**Does Bombadil ask what the machine is mostly for?** Default: no. The ten-second tour already ends with the chip "Make me something for what I do", which asks at the moment the answer pays off, and nothing consumes the answer on day one. Other options: one optional tap on the card (about three more seconds, to seed the first week's chips).

**Does Bombadil ask what to call itself?** Default: no. The OS is called Bombadil and says "I" or nothing. A renamed agent would touch every error line, brief and doc for no gain.

**Does the card suggest your name?** Default: no. The installer's account is called `user` with no full name, `claude auth status` prints no name or email, and `~/.claude.json`'s account block holds only an id and an email address, whose first part is often not a first name. A wrong name greeted every morning is worse than a blank field. Other options: prefill the local part of the email as a suggestion you confirm, if a real native login shows it carries a first name.

**How many voices?** Default: three (Merry, Plain, Quiet). Other options: four, with a dry deadpan one (a taste risk: its jokes are gated on true facts, but a joke next to a failure is hard to forbid by rule); two, a chatty-or-quiet switch. A warm reassuring voice is out, because a tone setting would tell the model to say what was left alone and what is safe, which it cannot check.

**Who writes the lines?** Default: local templates, no model call. A model-written greeting would add seconds and tokens to every boot, fail offline and before sign-in settles, be an uninvited turn, and drift off the voice. The model voices only its own closing sentence.

**Where do name and voice live?** Default: `~/.config/bombadil/persona.toml`, separate from config.toml and from memory.md, for the reasons under piece 1. "What do you know about me?" shows the name as its first row, read from that file, so there is one source for the name and no drift. Other options: the name as a line in memory.md (one memory, but two sources once agentd also needs it for local lines, and memory.md does not exist on any branch yet).

**How does the model learn the voice?** Default: one sentence in the system prompt, built when a command is built, for both providers. One build of Claude Code in a hosted sandbox kept the old appended prompt on `--resume`, which is why a change made mid-session rides that session's history, and why a VM check decides whether a per-turn line is needed after all. Other options: a block prepended to every turn (reaches a resumed session, but adds about 90 words per turn to a session that keeps them).

**How do you change it?** Default: by asking, and the one word "voice". The agent edits the two-key file with its ordinary file tools (no `set_voice` tool: there is no gate to reuse, because os-mcp cannot see the turn's words), and agentd validates on load. "be less chatty" and "explain less" are two different things: the first picks the voice, the second is the passenger brief's explain level, and no sentence sets both. Other options: a settings page; sliders for warmth and humour.

**What does a greeting say after a short break?** Default: only the facts, with no hello and no name, and only when there are facts. Greeting someone after every coffee makes the name a tic by the fifth time.

**Goodbye on restart?** Default: none. A restart is how a pending undo is applied, and "Restarting." with its undo text is what people need to read. The goodbye is for shutdown only.

**What happens when nobody answers the card?** Default: the defaults were saved when it appeared, the card folds on the first real ask or Esc, and it is never asked again. "call me Dan" stays available. A question that returns every boot is a dialog by another name.

**Do chips ride on the greeting?** Default: no. The three example chips on day one, and the habit chips after about 20 turns, stay exactly as the first brief decided, above the empty pill when you tap Super. The greeting neither carries nor removes them. The one exception is the time-zone chip on a UTC machine.

**Speech?** Default: writing only. A greeting that speaks, or a chime, belongs with the speech service and the sound defaults the earlier briefs set.

## Cut, and why

- **A setting for how Bombadil refers to itself.** The name is the product. Each voice decides "I" or nothing, and there is nothing for a setting to change.
- **A question about what the machine is mostly for.** Nothing consumes the answer on day one, and the tour's chip asks it when it pays off.
- **Sliders, or a long list of tones.** Named voices are chosen in one glance. Sliders invite tuning instead of choosing, and multiply the strings to write and test.
- **Warm, Dry and Steward voices.** Warm's reassurances ("nothing else on the machine changed") are claims no receipt backs, Dry's jokes are hard to keep off failures by rule, and Steward is Plain with longer words.
- **A model-written greeting.** It breaks the 200 ms rule, fails offline at boot and costs tokens for eight seconds of text.
- **Guessing the name from the account, the email, git config or the unix user.** Verified empty or wrong, see "Checked today".
- **A standing welcome, a splash, a clock or weather on the rest screen.** The rest state is wallpaper and a pill, and the desk brief already cut clock, weather and standing cards.
- **Unasked lines at other times:** an evening recap, an idle nudge, an hourly check-in, tips of the day, streaks and "day 30 with Bombadil". The agent never starts work on its own, and Time is the one place for what happened.
- **A recap of the day on an evening return, or "Today you installed Docker" in the goodnight.** The user knows what they did. The desk brief cut a card of the day's receipts for the same reason.
- **Voicing the working line, receipts, errors, limits, sign-out, offline, put-back lines, or narrated step verbs.** They are facts and safety marks. The first brief names chatty status lines as the failure mode.
- **Voice in coding sessions, and in memory.md.** A session is a program you run, not a second voice of the machine (dev brief rule 3), and every session loads memory.md.
- **A `set_voice` tool and its "the words asked for it" gate.** No such gate exists, and the agent can edit a two-key file.
- **A greeting on every unlock, a lock-screen greeting, a goodbye on restart.** Filler, or hidden behind the lock.
- **Users writing their own voices ("talk like a pirate").** The local lines could not follow, so the character would vanish at every boot. A model-only flavour can come later.
- **Voice variants in empty places.** They teach and are read by docs and agents.
- **Asking for the name again after it was skipped.** Nagging.

## Extends or changes the earlier briefs

- **First brief, piece 6 (first boot in the pill):** "Signed in. Ask me for anything." is replaced, on the first sign-in only, by the card and the chosen voice's first line. The ten-second tour, the Install chip and undo note, the three example chips and the faint day-one line are unchanged and keep their order. The faint line waits for the greeting to fade and ends the first time Super or Esc is pressed.
- **First brief, "A week later":** the welcome-back line keeps its decided words (Plain reproduces them) and gains a name and a voice. It is the same line as the return greeting, still built locally with no model call, and fades after a touch instead of "a few seconds".
- **First brief, "what skips the model":** gains one word, `voice`. The shutdown answer gains a goodbye clause, and restart is unchanged.
- **First brief decisions:** "Ask anything" stays the resting placeholder in every voice; agent text stays one line while working and at most four after. The voice may not lengthen either.
- **First brief, error wording:** unchanged in every voice.
- **Dev brief:** "While you were away" and the your-turn line are the same data and words. The greeting never repeats what the placeholder says. The presence file is an existence marker, so the return greetings need the idle signal in piece 2, which can be fed from its hypridle listener if the dev thread prefers. Rule 3 holds: no voice in sessions.
- **Desk brief:** the Away card is the list behind the greeting, leaves once seen, and when it is up the greeting shrinks to its opener and a count so nothing is said twice. No widget gets an empty state. No widget is added.
- **Brain brief:** Focus, the Map and Time use the empty-place format, and say how long the Brain has been watching.
- **Riding with Bombadil:** the counts it asked the line to carry (updates with their security count, promises that came true) are fact clauses here, and a page glyph for a fresh session can ride at the line's end when that piece lands. Its explain level (Brief, Normal, Teach) stays separate from the voice.
- **App kit (PR #2):** EmptyState gains `ask`. Its defaults ("Nothing here yet", "Nothing to show") stay as fallbacks.
- **PR #5 (native login):** the card rides its setup channel and its installer copy of ~/.config/bombadil. Without that copy, persona.toml is lost at install and the card would run again.
- **Installer:** stops forcing UTC over a zone chosen on the live USB.

## What would make it feel like a mascot instead

The same line every morning. The same joke in two places. A cheerful hello over an error. A recap of what you just did. A name in every sentence. A reassurance the machine cannot back ("nothing is lost"). Each has a rule above: the pool and the day count, facts worded the same in every voice, the priority order, the "news only" rule for what you made, the name in the opener only, and no claim the receipt does not make.

## Checked today

Checked on 29 to 30 Sep 2026 against main at d9dde3b and the branches named. Several items were found by the drafts' critics, who read the branches and ran commands in a scratch root.

- **The installer** (bombadil-install:51, :53) forces `/etc/localtime` to UTC inside the installed system, and creates the account with `useradd -m -G wheel,video,input,audio user` and no full name. Run against a scratch root, the account's name field is empty. The live image also links `/etc/localtime` to UTC, so every time-of-day word would be wrong for most people until a zone is set.
- **No name to prefill.** `claude auth status` (Claude Code 2.1.284 in the sandbox) prints loggedIn, authMethod and apiProvider and no name or email. `~/.claude.json`'s `oauthAccount` here holds accountUuid, emailAddress and organizationUuid only. One draft had assumed a `displayName`; it is not there. Codex's login status was not available to check.
- **The installer copy.** On main, `cp -ax /.` then `rm -rf /mnt/home/*` (bombadil-install:28) removes the live home, so nothing carries into the install. PR #5's branch copies `.claude`, `.claude.json`, `.codex` and `.config/bombadil` (line 68), which is the only reason persona.toml survives, and only once #5 merges.
- **Restore points do not cover /home yet.** The only snapper config is root on `/`. The first brief decided a /home config with exclusions, not built, and `~/.config/bombadil` is not among the exclusions, so a later Undo of a rename will work, and an Undo today will not.
- **The system prompt reaches both providers** on fresh and resumed turns at the command line (providers.py:103 for Claude's `--append-system-prompt`, :182 for Codex's `-c developer_instructions`, which appends where `instructions` replaces).
- **A resumed Claude session may ignore a changed appended prompt.** In the sandbox, with `--session-id` fixed, turn one used an appended prompt naming ALDER; turn two with `--resume` and a different appended prompt (BIRCH) still answered ALDER, while editing a CLAUDE.md between the turns did change the answer. One build, one sandbox, not reproduced elsewhere, and Codex is not installed here, so this is a VM check.
- **The line's modes** (PillState.qml on main): idle, working, closing, local. `dismiss()` is also called by the fade timer, so it cannot double as an Esc handler. Typing does not dismiss a local line today, and StatusLine's click handler is enabled only for the closing mode, so the welcome mode needs both.
- **Keyboard.** `summon()` toggles, and would hand the keyboard back if the pill already had it. PR #7 added a non-toggling `summonHere()` in shell.qml, which the card uses.
- **Shutdown.** The text shown while shutting down is `Launcher.doing()` ("Shutting down"), emitted before `systemctl poweroff`; launcher.py:470 returns the done text after it. The goodbye therefore goes in the start text, rendered in agentd because `doing()` is static.
- **Away.** hypridle and hyprlock are in no package list, and the dev brief's presence file records only present or absent. Quickshell's `IdleMonitor` has `timeout`, `isIdle` and `respectInhibitors`; whether Hyprland delivers idle notifications to it is to be checked.
- **turns.jsonl rows** carry t, prompt, result, ok, snapshot, provider, session, stopped, summary and details. There is no `made` or `changed` field, and no boot or login row, which is why piece 2 adds them. The summary is one past-tense sentence in the narrator's words ("Made Passwords."), and the verb is "made", not "built".
- **config.py:43 `save_user`** rewrites config.toml with only the provider and model, so the persona settings need their own file.
- **Widgets brief.** The file records that Daniel accepted the design and all fifteen defaults on 28 Sep 2026.
- **Open PRs:** #2 (app kit), #4 (coding sessions), #5 (native login) are drafts; #1, #3, #6 and #7 are merged.

**To verify in the VM before building:**
1. Resume behaviour: set the voice to Merry, run a turn, switch to Quiet, resume both providers, and confirm the new voice wins and the old one is not obeyed. If either keeps the old prompt, the persona goes to the front of each prompt through the existing `self.notes` prefix (agentd.py:409-413) as a single short line.
2. That the card takes the keyboard when the browser panel slides away, through `summonHere`, and that typing in the name field does not reach the pill.
3. That Quickshell's `IdleMonitor` reports idle under Hyprland, or that a hypridle listener can feed the same message.
4. How long the compositor survives `systemctl poweroff`, to see whether the goodbye is drawn, and whether it should wait for the end of the start text.
5. That the pill's clock follows a zone set with `timedatectl` without a restart of the shell.

## Open questions

- Who sets the time zone on a live USB with no network? The design degrades to "Welcome" and never states a wrong hour, but a person genuinely in UTC or GMT is indistinguishable from an unset machine.
- Should the explain level (Brief, Normal, Teach) live in persona.toml too, and should Quiet imply Brief? Today they are separate, and no sentence sets both.
- "voice" as a launcher word may sit next to the later push-to-talk feature. The decided triggers there are the mic button and Super+Space, not the word, but it is worth a second look when speech ships.
- Which screen shows the greeting on several monitors is the first brief's open question about the pill's screen, and this design follows it.

## The page

The mock-ups (the setup card you can type into, a switcher for the three voices, a day of lines, every empty place) are at https://claude.ai/artifact/2TpK6wRY3g11oq7vRaqAgp, and its HTML is saved as design/pages/bombadils-voice.html.

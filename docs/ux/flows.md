# Flows

> **Status:** Partly shipped
> **Code:** `src/bombadil/agentd.py`, `src/bombadil/launcher.py`, `src/bombadil/providers.py`, `src/bombadil/config.py`, `src/bombadil/signin.py`, `src/bombadil/browser.py`, `src/bombadil/hypr.py`, `src/bombadil/snapshots.py`, `src/bombadil/narrate.py`, `src/bombadil/procs.py`, `src/bombadil/watch.py`, `src/bombadil/desk.py`, `src/bombadil/vitals.py`, `src/bombadil/rest.py`, `src/bombadil/finder.py`, `src/bombadil/cards.py`, `src/bombadil/sysmap.py`, `src/bombadil/cardtools.py`, `src/bombadil/notices.py`, `src/bombadil/outbox.py`, `src/bombadil/mail/watch.py`, `src/bombadil/mail/tools.py`, `src/bombadil/appkit/tools.py`, `src/bombadil/appkit/placement.py`, `shell/shell.qml`, `shell/PillState.qml`, `shell/StatusLine.qml`, `shell/QueueChips.qml`, `shell/SetupChips.qml`, `shell/FoundChips.qml`, `shell/AiCard.qml`, `shell/NoticeChips.qml`, `shell/CardHost.qml`, `shell/DeskState.qml`, `shell/DeskDrag.qml`, `shell/DeskRail.qml`, `shell/DeskStrips.qml`, `share/apps/mail/app.py`, `share/apps/mail/SendBar.qml`, `bin/bombadil`, `bin/bombadil-browser`, `iso/airootfs/usr/local/bin/bombadil-setup`, `iso/airootfs/usr/local/bin/bombadil-install`, `iso/airootfs/usr/local/bin/bombadil-rollback`, `iso/airootfs/etc/skel/.config/hypr/hyprland.lua`, `iso/airootfs/etc/systemd/user/bombadil-agentd.service`, `iso/airootfs/etc/systemd/user/bombadil-shell.service`, `iso/airootfs/etc/systemd/user/mako.service.d/restart.conf`, `scripts/test-vm.sh`
> **Design:** [How Bombadil should feel](../design/ux-brief.md#the-rules), [Riding with Bombadil](../design/passenger-brief.md#the-rules), [The desk](../design/widgets-brief.md#the-rules), [Bombadil, installed](../design/installed-os-brief.md#the-rules)
> **Verified:** 2026-11-08 against `main` at `7176e55`

A flow is one thing a person does, followed from the first key to the last thing they see. This page follows sixteen: first boot, signing in, asking for something, a package step, stopping, queueing, undo, building an app, the browser, the details drawer, the launcher words, the desk, installing, the AI resting, mail and a picture card. Each section says what the person wants, draws the components and messages that carry it, lists what is on screen at each step and then lists what the person sees when it goes wrong. The surfaces and the stone's states are described once, in [the interaction model](README.md#the-surfaces), and the keys and timings behind them in [keys and timings](keys-and-timings.md); this page links to them instead of repeating them. The pictures in `docs/screens/pill/` and `docs/screens/resting/` come from the headless desktop test: it starts the real bar, agentd and the Claude Code CLI in a headless compositor against a scripted stand-in for the API (`tests/desktop/driver.py`, `tests/desktop/fake_api.py`), so the model's side of every picture is scripted (`install ffmpeg`, `make me a password manager`, `set up docker` and `tell me a joke` each have a script), while the launcher words and the `!` command in them are answered by the real agentd. The two pictures in `docs/screens/vm-fixes/` come from the VM smoke run instead (`docs/screens/vm-fixes/drawer-smoke.log`). The dot at the pill's left end in the `pill/` and `vm-fixes/` pictures stands in for the stone that `shell/Stone.qml` draws; the six `resting/` pictures show the stone. A step that is only designed says Designed and links its brief.

## 1. First boot and setup

**The person wants** to turn the computer on and start using it with an AI account they already have.

```mermaid
sequenceDiagram
    actor U as person
    participant G as greetd
    participant H as Hyprland
    participant S as systemd user manager
    participant A as agentd
    participant P as the pill
    G->>H: start-hyprland as user, no login screen
    H->>S: start hook: import the environment, restart agentd, the shell and mako
    S->>A: start the unit
    A->>A: no user config file, so access is choose
    S->>P: start the bar
    P->>A: connect over the socket
    A-->>P: status, entries, setup (choose)
    U->>P: tap Claude or Codex
    P->>A: setup_action provider:claude
    A->>A: save the choice, check_access
    A-->>P: setup (signing_in or ready)
```

1. The image starts the desktop as the user `user` without a login screen (`iso/airootfs/etc/greetd/config.toml`; `iso/airootfs/etc/systemd/system/bombadil-live.service` creates the user with an empty password). The `hyprland.start` hook in `iso/airootfs/etc/skel/.config/hypr/hyprland.lua` runs `systemctl --user import-environment && systemctl --user restart bombadil-agentd bombadil-shell mako`. The three are user services (`iso/airootfs/etc/systemd/user/bombadil-agentd.service`, `bombadil-shell.service` and the drop-in `mako.service.d/restart.conf`) that systemd starts again one second after they die. The hook is the only thing in this repository that starts them at login; `bombadil-setup` can restart agentd by hand (step 6), and the `mako` package's own unit may start `mako` in other ways ([the session stays alive](../architecture/session-services.md#the-five-units)).
2. The pill appears with the placeholder `Starting` and the stone in its `starting` face until agentd answers.
3. agentd has no user config file (`~/.config/bombadil/config.toml`) and `BOMBADIL_PROVIDER` is not set, so it is not "chosen" and its setup state is `choose`. The line reads `Which AI should run this computer?`, two big chips read `Claude` and `Codex`, the stone is in its `needs` face and the placeholder is `Ask anything`.

   ![The first-boot question over a black desk](../screens/boot-and-wallpaper/before-black-desk.png)

   *The question, the two chips and the pill on a black ground. The picture is from the boot work in [boot and the wallpaper](../design/boot-and-wallpaper.md).*

4. The person taps a chip, or types `claude` or `codex` into the pill: while the state is `choose`, `AgentD.handle` reads a provider name as the answer. agentd writes `provider = "<name>"` to `~/.config/bombadil/config.toml` (`config.save_user` writes `provider`, keeps `explain` and the `[models]` table, and writes `model` only if the config file named one), and checks whether that provider's CLI is signed in.
5. Signed in: the line reads `Claude is ready. Ask me for anything.` and fades by itself. Not signed in: the sign-in starts at once and the person is in [flow 2](#2-signing-in-to-a-provider-from-the-pill).
6. The terminal fallback is `bombadil-setup`, run by hand from a terminal (Super+Return). It asks `Which AI should run this computer?` with a numbered list, installs the CLI with `sudo npm install -g` if it is missing, asks agentd to pick and sign in (`bombadil provider <name>`), and, when agentd does not answer, writes the config file itself, runs the CLI's own login with `BROWSER=bombadil-browser` and restarts the agentd unit (`systemctl --user restart bombadil-agentd`). Nothing on `main` starts it.

Wi-Fi networks as chips with the password typed in the pill, three example chips and a first-day hint line are Designed ([How Bombadil should feel, build these first](../design/ux-brief.md#build-these-first)). The two earliest pictures of the desk, `docs/screens/first-boot/desktop-bar.png` and `docs/screens/vm-fixes/drawer-closed-by-esc-in-pill.png`, show an earlier terminal wizard instead of this question; [the interaction model](README.md#screens) says so.

### Where it breaks and what the person sees

| What goes wrong | What the person sees | Code |
|---|---|---|
| agentd has not started yet | `Starting` and a rolling stone for the length of the boot window, then the `offline` face and the placeholder `Waiting for agentd…`. The bar keeps trying the socket ([keys and timings](keys-and-timings.md#the-bar)). Enter shows `Not connected to the agent yet.` and the text stays in the field | `shell/shell.qml`, `shell/PillState.qml` (`submit`, `_offline`) |
| The chosen CLI is not installed (the image installs both, a development machine may not) | Nothing at the choice: the state goes to `ready` with no line. The first ask ends with the error `claude is not installed yet: press Super+Return and run bombadil-setup` | `src/bombadil/agentd.py` (`check_access`, `turn`), `scripts/build-iso.sh` |
| The person types something else while the question is up | The text is queued as a prompt and the setup line stays. It runs after the choice and the sign-in | `src/bombadil/agentd.py` (`handle`, `_runnable`) |
| The person presses Esc or waits | The question stays: only finished lines fade or go with Esc, and a setup line is not one. A launcher answer replaces it for a few seconds and it comes back | `shell/PillState.qml` (`_putLineAway`), `shell/StatusLine.qml` |
| `bombadil-setup` when agentd does not answer, and the CLI's login fails (no network, say) | The script stops there with the CLI's own message (`set -e`), before it restarts the agentd unit and before it prints `Done. Type in the pill at the bottom of the screen.` | `iso/airootfs/usr/local/bin/bombadil-setup` |

## 2. Signing in to a provider from the pill

**The person wants** to connect their Claude or Codex account without leaving the screen and without copying a code.

```mermaid
sequenceDiagram
    actor U as person
    participant P as the pill
    participant A as agentd
    participant C as provider CLI in a pty
    participant B as bombadil-browser
    participant W as browser panel
    U->>P: tap Sign in, or type sign in
    P->>A: setup_action signin
    A->>C: run the login with BROWSER=bombadil-browser
    C->>B: open the sign-in URL
    B->>A: open_url, with the sign-in id
    A->>W: new tab on port 9222, slide the panel in
    U->>W: sign in on the provider page
    W->>C: the page returns to localhost
    C-->>A: exits 0, signed_in confirmed
    A-->>P: setup ready, Signed in to Claude. Ask me for anything.
```

The way in is any of: the first-boot chip ([flow 1](#1-first-boot-and-setup)), the `Sign in` chip while signed out, the words `sign in`, `log in`, `login` and their variants, `use codex`, `switch to claude` and `sign in to codex`, a prompt typed while signed out (the prompt waits and the sign-in starts), and `bombadil signin [provider]` in a terminal.

1. agentd first checks that the provider's sign-in host answers (`platform.claude.com:443` or `auth.openai.com:443`). The line reads `Opening the Claude sign-in` with a `Cancel` chip, and the stone turns (`working`).
2. `signin.SignIn` runs the CLI's login in a pseudo-terminal 50 rows by 1000 columns, with `$BROWSER` set to `bin/bombadil-browser`. The CLI hands its URL to that command, which sends `open_url` to agentd; agentd opens the page in the browser panel ([flow 9](#9-the-browser-panel-and-a-link-from-the-terminal)). The browser panel slides in with the page, and the line reads `Sign in to Claude in the browser`, still with `Cancel`.

   ![The browser panel over the desk](../screens/first-boot/browser-panel.png)

   *The panel as it looks when it slides in. It is a search page, not a sign-in page: no picture of a sign-in is on `main`.*

3. The person signs in on the page. Claude's automatic flow and Codex return to a small server the CLI runs on localhost; the line reads `Finishing the Claude sign-in`. Claude's manual flow ends on a page that holds a code in the tab's address (`code#state`): agentd reads the address through the debugging port and types the code into the CLI's prompt itself, so nothing is copied.
4. The CLI exits 0 and agentd confirms with the provider's own check. The panel slides away and the page's tab closes unless it is the last one. The line reads `Signed in to Claude. Ask me for anything.` and fades by itself. Prompts that waited for the sign-in run, in the order they were typed.

### Where it breaks and what the person sees

| What goes wrong | What the person sees | Code |
|---|---|---|
| No network | `No internet. Connect to a network to sign in to Claude.` in an error tone, the stone in its `needs` face, chips `Wi-Fi` and `Try again`. `Wi-Fi` opens `nmtui connect` in the details drawer (`Opened Wi-Fi.`), or says `Wi-Fi settings need NetworkManager, which is not installed.` agentd keeps looking and starts the sign-in by itself when the host answers | `src/bombadil/agentd.py` (`start_signin`, `_wait_online`, `OFFLINE_POLL`), `src/bombadil/launcher.py` (`_wifi`) |
| The person hides the panel (Super+B) | `The Claude sign-in is waiting in the browser`, chips `Show sign-in` and `Cancel` | `src/bombadil/agentd.py` (`_describe`), `src/bombadil/signin.py` (`_look`) |
| The person closes the page's tab, or the browser | `The Claude sign-in page was closed`, chips `Open it again` and `Cancel`. The CLI is still waiting; the chip opens the address in a new tab | `src/bombadil/signin.py` (`show`) |
| The browser is slow, missing or will not open the page | The sign-in waits anyway, because the CLI does. The line says the page was closed when no tab shows. If the CLI then fails, the reason names the browser trouble when the CLI said nothing | `src/bombadil/signin.py` (`_maybe_open`, `_trouble`), `src/bombadil/browser.py` |
| Cancel (the chip, Esc, the word `stop` or `cancel`) | `Cancelling the Claude sign-in`, then `Sign-in cancelled.` Prompts that waited for it are dropped and their chips go. If the stored login is still good, the state returns to ready without a line | `src/bombadil/agentd.py` (`stop`, `_run_signin`, `_drop_waiting`) |
| The sign-in waits on its page past its time limit | `The Claude sign-in timed out.` in an error tone with the chips `Sign in` and `Use Codex instead`. `BOMBADIL_SIGNIN_TIMEOUT` sets the limit ([keys and timings](keys-and-timings.md#agentd-and-the-launcher)) | `src/bombadil/signin.py` (`TIMEOUT`), `src/bombadil/agentd.py` (`_describe`, `_run_signin`) |
| The CLI exits with an error | `Could not sign in to Claude: <reason>.` and the same two chips. The page stays on screen so the person can read it | `src/bombadil/signin.py` (`run`), `src/bombadil/providers.py` (`SIGNIN_ERRORS`) |
| Codex when the person is already signed in | `You are already signed in to Codex.` A new `codex login` revokes the stored login as it starts, so agentd does not run it | `src/bombadil/agentd.py` (`signin_asked`), `src/bombadil/providers.py` (`login_replaces`) |
| The login is gone in the middle of a turn | `Claude signed you out.` and the sign-in starts. The prompt goes back to the front of the queue once and runs after the sign-in. If it runs again and gets the same answer, the CLI's own words show and agentd does not loop | `src/bombadil/agentd.py` (`_signed_out_turn`) |
| Someone runs `/login` in a terminal while agentd says signed out or offline | agentd sees the sign-in URL arrive, shows the page in the panel and watches for the login file to change. The line reads `Sign in to Claude in the browser` | `src/bombadil/agentd.py` (`open_url`, `_watch_external`) |
| The person switches provider (`use codex`) | The old sign-in ends, the choice is saved, the other provider is checked, and the conversation starts again because it belongs to the first provider. A model chosen with `use opus` is dropped too | `src/bombadil/agentd.py` (`choose`) |

## 3. Asking for something

**The person wants** to say what they want in a sentence and have the machine do it, knowing it can be taken back.

```mermaid
sequenceDiagram
    actor U as person
    participant P as the pill
    participant A as agentd
    participant S as snapper
    participant C as provider CLI
    U->>P: type a sentence, Enter
    P->>P: the line reads On it
    P->>A: prompt
    A-->>P: queued, then event turn_start
    A->>S: sudo snapper create, turn n and the prompt
    A-->>P: event snapshot
    A->>C: start in a systemd scope, prompt on stdin
    C-->>A: stream of steps and results
    A-->>P: events status, tool, tool_result
    A-->>P: event turn_end with line and summary
```

1. The person taps Super (or presses Alt+Space, or clicks the pill; the keys are in [keys and timings](keys-and-timings.md#keys)), types and presses Enter. `PillState.submit` shows `On it` before agentd has answered, and the pill gives the keyboard back.

   ![install ffmpeg typed in the pill](../screens/pill/01-typed.png)

   *The scripted prompt in the field, with the stone's stand-in dot still green.*

   ![On it above the pill](../screens/pill/02-on-it-150ms.png)

   *`On it` above the pill, a moment after Enter in the scripted run.*

2. agentd sends `turn_start` first, so something true is on screen before snapper runs. Where snapper is configured (`/etc/snapper/configs/root`), the line reads `Saving a restore point` while snapper creates `turn:<n>: <prompt>` (the start of the prompt) in the `number` cleanup class, and `snapshot` carries its number. The installer configures snapper to keep at most 30 of them when its cleanup timer runs ([flow 13](#13-install-to-disk)). The restore point is the one `Undo` uses ([flow 7](#7-undo), [restore points](../architecture/restore-points.md#retention-and-pruning)).
3. agentd starts the provider CLI in its own systemd scope and writes the prompt to its standard input. It sets `BROWSER`, `BOMBADIL_TURN` and `BOMBADIL_SOCKET` for the turn, and `BOMBADIL_TURN_SNAPSHOT` when a restore point was taken. Claude is asked for Sonnet (`--model claude-sonnet-5-5`, from `config.DEFAULT_MODELS`) unless the config file names a model or the person said `use opus` ([flow 11](#11-the-launcher-words-and-completing-with-tab)); Codex has no default model here and keeps its own. The turn's log row records the model. The line reads `Waiting for Claude` until the first word comes.
4. The line follows the agent's work in the present tense: `Installing ffmpeg`, `Building Passwords, 12 lines`. Seconds appear after the first. A step that touches the system gets an amber edge and a step no restore point can undo gets a red edge, each with its exact command ([flow 4](#4-a-system-step-that-needs-a-package)). A plan of two steps or more, or a step that touches the system, also fills the `Now` card on the desk ([desk](../architecture/desk.md)).
5. The turn ends. The line is the first of: `Stopped while …` for a stop, the cut-off line when the account's limit stopped the turn halfway ([flow 14](#14-the-ai-resting)), the error when the turn failed or had no reply, the agent's reply, the narrator's summary (`Updated the system and installed ffmpeg.`), `Done.`. When the turn changed something, the line stays until the next prompt and carries `Undo` and `Details`. When nothing changed it fades by itself ([keys and timings](keys-and-timings.md#the-line-and-the-picture)). A receipt picture follows a turn that changed the network, the disks, the sound, the screens or a service.

   ![The finished line with Undo and Details](../screens/pill/04-ffmpeg-done.png)

   *The scripted reply on the closing line, with `Undo` and `Details` at its lower right.*

### Where it breaks and what the person sees

| What goes wrong | What the person sees | Code |
|---|---|---|
| agentd is not there | Enter shows `Not connected to the agent yet.` in the line and the text stays in the field. A press on `Undo`, `Details`, a chip, the `×` of a queued ask or the stone flashes the same sentence for a moment. Esc flashes it only while a sign-in is showing: Esc is Stop only for a running turn or a sign-in, and a bar that has lost agentd has no running turn, so the stone is not a Stop button either | `shell/PillState.qml` (`submit`, `_offline`, `lost`, `stoppable`) |
| agentd restarts during a turn (a crash, which systemd answers one second later, or `systemctl --user restart bombadil-agentd`) | `Lost touch with the agent. Reconnecting.` for 8 s while the bar keeps retrying the socket, and then the line fades. The new agentd has no turn running and an empty queue, so its first `status` has `busy` false and the line does not change to `Working`. The conversation and the session's model carry on (see `The conversation is gone` below) | `shell/PillState.qml` (`lost`, `handle`), `shell/shell.qml`, `src/bombadil/agentd.py` (`_load_state`) |
| The bar restarts while agentd runs a turn (`systemctl --user restart bombadil-shell`, Super+Ctrl+Esc) | The first `status` it gets has `busy` and the turn's number, so the line reads `Working` and the turn goes on | `shell/PillState.qml` (`handle`) |
| The AI is not ready (first boot, signed out, offline) | The prompt waits as a `next` chip, the setup line stays, and the prompt runs when the state is `ready` ([flow 2](#2-signing-in-to-a-provider-from-the-pill)). A prompt that starts with `!` does not wait | `src/bombadil/agentd.py` (`_runnable`), `shell/PillState.qml` (`submit`) |
| The provider CLI is missing | The error `<binary> is not installed yet: press Super+Return and run bombadil-setup` on the closing line, with no step run | `src/bombadil/agentd.py` (`turn`) |
| snapper fails | The turn still runs, with no restore point. The error `no undo point for this turn: snapper failed (…)` reaches the line only when the turn has no reply; otherwise it is in `Details` only | `src/bombadil/agentd.py` (`turn`), `shell/PillState.qml` (`turn_end`) |
| No snapper (the live image, a development machine, `snapshots = false`) | No `Saving a restore point` step and no restore point. The `Undo` button still appears on a changed turn, because no shell file reads the `snapshots` flag of `status`, and pressing it answers as [flow 7](#7-undo) says | `src/bombadil/snapshots.py` (`available`), `shell/StatusLine.qml` |
| The provider goes quiet | After a quiet spell with no output and no tool running (six times longer for Codex): `Claude is not answering; check the connection`. The line returns to `Thinking` when output resumes | `src/bombadil/agentd.py` (`watchdog`, `NO_PROGRESS_SECS`), `src/bombadil/providers.py` (`quiet_factor`) |
| The account's limit | A used-up plan window or spending cap, which `providers.limit()` recognises, does not end the turn with an error. The turn is cut off, the ask goes back to the front of the queue under the same id, the machine rests and the line says when the AI is back ([flow 14](#14-the-ai-resting)). Only a failed result that names a limit but that `limit()` does not recognise as the account's gets `This account has hit a usage or spending limit. Try again after it resets, or raise the limit.` and the first line of the provider's own message. A rate limit says `The provider is rate limiting requests; try again in a minute.` | `src/bombadil/agentd.py` (`_refusal`, `_refused`, `_rest_turn`, `_limit_text`), `src/bombadil/providers.py` (`limit`) |
| The conversation is gone | The error gets the suffix `(the previous conversation is gone; the next prompt starts a new one)`. agentd keeps the conversation id and the session's model in `agentd-state.json` under the runtime directory (`paths.runtime_dir()`, `/run/user/<uid>/bombadil`). They survive an agentd restart and are lost at a reboot or a new login, which clear that directory | `src/bombadil/agentd.py` (`_on_event`, `_load_state`, `_save_state`) |
| A command fails inside the turn | `Details` shows `│ failed (exit N)` under a step that failed by itself; a command that Stop ended shows `│ stopped` instead ([flow 5](#5-stopping-a-turn)). The narrator counts a step as done when it starts, so a turn with no reply from the agent can still close with `Updated the system and installed ffmpeg.` and an `Undo` button after a failed install | `src/bombadil/narrate.py` (`Narrator._set`, `summary`), `src/bombadil/watch.py` (`STOP_CODES`) |

## 4. A system step that needs a package

**The person wants** a program the computer does not have, installed without being asked for permission and without a surprise.

```mermaid
sequenceDiagram
    participant C as provider CLI
    participant A as agentd
    participant N as narrate.py
    participant P as the pill
    C->>A: tool call, sudo pacman -Syu --noconfirm --needed ffmpeg
    A->>N: Narrator.on_event
    N-->>A: Updating the system and installing ffmpeg, risk system, the command
    A-->>P: event status with risk, command, because
    P->>P: amber edge, command under the line
    C-->>A: tool_result, then the reply
    A-->>P: event turn_end, summary Updated the system and installed ffmpeg.
    P->>P: Undo and Details on the line
```

The system prompt tells the agent to install with `sudo pacman -Syu --noconfirm --needed <packages>`, never `pacman -Sy` alone, and, because that also updates every other package, to say so in one plain sentence before it runs it; to say in one short sentence why before each step that changes the machine; to tell the person a restart is needed when an upgrade replaced the kernel; to set the time zone with `sudo timedatectl set-timezone <Area/City>` when the person names one; and to reply in at most four lines (`system_prompt` in `src/bombadil/providers.py`). Nothing pauses for permission: both CLIs run with full access, and the marks exist so that Esc is in reach.

1. The restore point was taken at the start of the turn ([flow 3](#3-asking-for-something)). Before the command runs, the line reads `Updating the system and installing ffmpeg` with an amber edge on its left and the exact command under it in monospace, cut when it is long. The `-Syu` form is read as an update plus an install (`_pkg_step` in `src/bombadil/narrate.py`); a command with `-S` and no `u` reads `Installing ffmpeg`.

   ![Installing ffmpeg with an amber edge and the command](../screens/pill/03-installing-ffmpeg.png)

   *The scripted run's command is `sudo pacman -S --noconfirm --print ffmpeg; sleep 4`, a dry run with a pause, so nothing is installed. It uses `-S`, not the `-Syu` form the agent is told to use, so the picture reads `Installing ffmpeg`, and the line is narrated as an install all the same, as the first row of the table below says.*

2. The agent's own sentence from just before the step, kept short, shows under the command (`because`). On a marked step it is always shown; on other steps it shows when the pointer rests on the line. After the machine read something from outside (a web page, say), a marked step also says `after reading <what>`, by order and never by guessed cause.
3. A step that cannot be undone, such as `mkfs`, `dd` to a device, `wipefs`, `sgdisk -Z`, `rm` under the home folder or `git push --force`, gets a red edge. `rm` under home is marked because home is in no restore point.
4. The turn ends with the agent's reply, or `Updated the system and installed ffmpeg.` when there is none. `Undo` and `Details` stay on the line. A turn with a red step also shows `can’t be undone`, and `Details` ends with `One step here cannot be undone.`

   ![Installing docker with an amber edge over an open window](../screens/pill/11-docker-system-step.png)

   *A second scripted system step, `sudo sh -c 'echo installing docker; sleep 120'`, read as `Installing docker`.*

### Where it breaks and what the person sees

| What goes wrong | What the person sees | Code |
|---|---|---|
| A dry run is read as an install | `pacman -S --print ffmpeg` is narrated as `Installing ffmpeg` and summarised as `Installed ffmpeg.` (with `-Syu` it reads `Updating the system and installing ffmpeg`): `_pkg_step` does not look at `--print` | `src/bombadil/narrate.py` (`_pkg_step`) |
| The install fails (no network, no such package) | `Details` shows the output and `│ failed (exit N)`. The closing line is the agent's reply, which names the failure. With no reply it falls back to `Updated the system and installed ffmpeg.` | `src/bombadil/narrate.py`, `src/bombadil/watch.py`, `shell/PillState.qml` |
| The agent runs `pacman -Sy` alone | Nothing stops it. The narrator reads it as `Refreshing the package lists`. The rule is in the system prompt only | `src/bombadil/providers.py` (`system_prompt`), `src/bombadil/narrate.py` |
| The agent runs a command the narrator has no words for | The line reads `Running <program>` or the agent's own description, still amber when `sudo` or a system path is involved | `src/bombadil/narrate.py` (`shell_step`) |
| A kernel upgrade | The agent is told to say that a restart is needed; the line shows its reply. The kernel lives on the EFI partition, outside every restore point, so an undo after a kernel update can pair a newer kernel with older modules | `src/bombadil/providers.py`, [restore points](../architecture/restore-points.md) |

## 5. Stopping a turn

**The person wants** the machine to stop now, whatever it is doing, without breaking anything.

```mermaid
sequenceDiagram
    actor U as person
    participant P as the pill
    participant A as agentd
    participant S as procs.Stopper
    participant T as the turn's scope
    U->>P: Esc, Stop under the pointer, or Super+Escape
    P->>A: stop
    P->>P: the line reads Stopping
    A->>A: stopping is true, stopped line kept
    A->>S: stop(pid, unit)
    S->>T: SIGINT, wait 3 s
    S->>T: SIGTERM, wait 1 s, SIGKILL
    A-->>P: event turn_end, stopped, Stopped while installing docker.
```

The ways in: Esc in the pill, the `Stop` that the stone turns into under the pointer, the word `stop` (or `cancel`, `stop it`, `stop that`), Super+Escape (`bombadil stop`) and the setup chip `Cancel`. A turn can be stopped from the moment Enter shows `On it`, before agentd has confirmed it ([the interaction model](README.md#the-stone-and-the-pills-states)). The keys are in [keys and timings](keys-and-timings.md#keys).

1. The line reads `Stopping` at once; `PillState.stop` sets it before agentd answers, and agentd then sends its own `Stopping` status.
2. agentd signals everything the turn started. Each turn runs in a systemd scope named `bombadil-turn-<agentd pid>-<turn>-<start time>`, so the stopper finds processes that daemonized or were reparented. SIGINT goes first, so the CLI saves its conversation and `sudo` passes the signal on; after 3 s the rest gets SIGTERM, and after one more second SIGKILL.
3. If pacman is running, the line first reads `Stopping after this package`. pacman killed mid-package leaves files its database does not know, so the stopper freezes the rest of the turn with SIGSTOP, sends SIGINT to pacman alone, waits up to 900 s for it to finish the package and exit, and only then stops the rest. pacman and its hooks never get SIGTERM or SIGKILL. A lock file it leaves behind (`/var/lib/pacman/db.lck`) is removed once no pacman runs.
4. The line becomes `Stopped while installing docker.`, or `Stopped.` when no step had started. The stone shows its `stopped` face. `Undo` is not offered after a stop, because a stopped turn is not sticky; `Details` is, when the turn changed something. In `Details` a command that Stop ended shows `│ stopped`, not `│ failed (exit N)`: `watch.STOP_CODES` holds the exit codes `-2`, `-15` and `-9` that SIGINT, SIGTERM and SIGKILL leave.

   ![Stopped while installing docker](../screens/pill/14-stopped.png)

   *The stopped line of the scripted run. The queued prompt's chip is gone and no button shows. The picture before it, `docs/screens/pill/13-stopping.png`, looks the same, so no picture shows the `Stopping` moment.*

5. A queued prompt starts at once ([flow 6](#6-queueing-asks-while-one-runs)), and the stopped line stays over it for a moment.
6. Windows the turn opened are left alone, because they are the person's from then on: the browser panel (`--class=bombadil-browser`), the terminal (`--app-id=bombadil-terminal`), the details drawer (`--app-id=bombadil-details`), Files (`nautilus`) and apps (`bombadil-app run <name>`).

### Where it breaks and what the person sees

| What goes wrong | What the person sees | Code |
|---|---|---|
| Stop arrives while the restore point is being saved | The turn ends before the CLI starts, and the line reads `Stopped.` | `src/bombadil/agentd.py` (`turn`) |
| Stop arrives while the CLI is starting | The stopper runs as soon as the process exists | `src/bombadil/agentd.py` (`turn`, `_stop_proc`) |
| A command is ended by Stop | `Details` shows `│ stopped` under its step. `│ failed (exit N)` is for a command that failed by itself | `src/bombadil/watch.py` (`STOP_CODES`, `Renderer.one`) |
| Nothing is running | The word `stop` answers `Nothing is running.` Esc clears typed text, and with the field empty it puts the line, the picture and the drawer away | `src/bombadil/agentd.py` (`local`), `shell/shell.qml` |
| A sign-in is under way | Esc cancels it ([flow 2](#2-signing-in-to-a-provider-from-the-pill)) | `src/bombadil/agentd.py` (`stop`) |
| A step runs as root | The stopper signals root processes with `sudo -n kill` when there is no `sudo` above them to relay the signal | `src/bombadil/procs.py` (`_signal`) |
| pacman has not stopped after 900 s | It is left alone, never killed, and the rest of the turn is stopped; its lock is not removed while it runs | `src/bombadil/procs.py` (`Stopper.stop`) |
| The stopper itself fails | The error `stop: <reason>` appears, and the CLI is killed outright so the turn never keeps running | `src/bombadil/agentd.py` (`_stop_proc`) |
| agentd is gone | In the pill, `Not connected to the agent yet.` Super+Escape prints `agentd is not running (<reason>)` to standard error and exits 1, which a key bind does not show | `bin/bombadil` (`send`), `shell/PillState.qml` |

## 6. Queueing asks while one runs

**The person wants** to think of the next thing while the machine is busy and have it happen after, without waiting at the keyboard.

```mermaid
sequenceDiagram
    actor U as person
    participant P as the pill
    participant A as agentd
    U->>P: type a second sentence, Enter
    P->>A: prompt
    A-->>P: queued, turn 2
    A-->>P: event queued, turn 2, the prompt
    A->>A: pending holds turn 2
    P->>P: a grey next chip with a cross
    A-->>P: event turn_end for turn 1
    A-->>P: event turn_start for turn 2, the chip leaves
```

1. While a turn runs, the pill still takes text. Enter sends the prompt and the pill does not show a second `On it`.
2. agentd accepts it at once (`queued` to the sender) and tells every bar (`queued` event) because a turn is running. A grey chip reads `next`, the prompt and `×`, between the line and the pill. There is one chip per waiting prompt, in the order they will run. While the AI rests the chip says when the ask runs (`06:24`, `Thu 09:00`, `paused`) in place of `next` ([flow 14](#14-the-ai-resting)).

   ![A next chip between the line and the pill](../screens/pill/12-queued-chip.png)

   *The scripted `tell me a joke` waiting behind the `Installing docker` step.*

3. The `×` sends `unqueue`; agentd drops the prompt and sends `unqueued`, and the bar removes the chip at once without waiting for it.
4. When the running turn ends, or is stopped, the next prompt starts at once and its chip leaves with `turn_start`.

Words the launcher knows are never queued: they are answered at once, also while a turn runs ([flow 11](#11-the-launcher-words-and-completing-with-tab)). `undo`, `restart` and `shutdown` stop the running turn first and hold the queue until they are done ([flow 7](#7-undo)).

### Where it breaks and what the person sees

| What goes wrong | What the person sees | Code |
|---|---|---|
| The AI is not ready | Prompts queue the same way, with the setup line above them. When sign-in is cancelled they are dropped and their chips go; when it succeeds they run in order | `src/bombadil/agentd.py` (`handle`, `_drop_waiting`) |
| Stop is pressed | The running turn stops and the next prompt starts. Stop does not clear the queue; each chip needs its `×` | `src/bombadil/agentd.py` (`stop`, `_worker`) |
| The login was lost in a turn | The prompt that found it lost goes back to the front of the queue once, with its chip, and runs after the sign-in | `src/bombadil/agentd.py` (`_signed_out_turn`) |
| agentd restarts | The queue is held in memory (`AgentD.pending`), so it is empty after a restart and the chips go with the first `status` | `src/bombadil/agentd.py`, `shell/PillState.qml` (`_setQueue`) |

## 7. Undo

**The person wants** to take back what the machine just did to the system.

```mermaid
sequenceDiagram
    actor U as person
    participant P as the pill
    participant A as agentd
    participant L as Launcher
    participant R as sudo bombadil-rollback
    U->>P: tap Undo, or type undo
    P->>A: local undo, or prompt undo
    A->>A: hold the queue, stop the running turn first
    A->>L: run the undo action
    L->>L: pick the newest turn restore point before the marker
    L->>R: swap @ for that snapshot at the next boot
    L->>L: write undo.json with the boot id
    A-->>P: event local, Undone. System files go back to before ...
```

1. A turn that changed something ends with `Undo` and `Details` on its line. The words `undo`, `undo that`, `undo it` and `undo the last change` do the same, at any time.
2. The line reads `Undoing the last change`, then the answer, which stays longer than most lines so that it can be read: `Undone. System files go back to before “install ffmpeg” when you restart. Your home folder and apps stay as they are.` The quoted words are the prompt in the restore point's description.
3. The system keeps running as it was. `bombadil-rollback N` makes snapshot `N` the root from the next boot on, and keeps the old root as `@.undone-<time>`. The word `restart` (`Restarting.`) applies it.
4. A second undo before a new turn goes one turn further back. An undo before the restart says `Already undone: when you restart, system files go back to before “…”, and that takes back everything since too.`, once, and the undo after that goes further back.

   ![Undo is off here, in red](../screens/pill/19-undo.png)

   *The answer where no restore points exist, which is the case in the scripted run. The line has a red edge.*

5. Restore points are pruned. The installer sets snapper's `root` config to `NUMBER_CLEANUP=yes` and `NUMBER_LIMIT=30` with no hourly points, and enables `snapper-cleanup.timer`, so an installed system keeps at most 30 numbered restore points once the timer has run; more can exist between two runs, and the smoke checks the settings and the enabled timer, not a deletion ([flow 13](#13-install-to-disk), [restore points](../architecture/restore-points.md#retention-and-pruning)). Undo can go back only as far as snapper keeps, about 30 turns.

If the turn that is running is the one to undo, undo ends it first. While an undo runs, no queued turn starts, so it cannot take the restore point the undo needs. A sentence such as `please undo that` is not a launcher word: it goes to the agent, whose `rollback` tool takes the last turn's restore point and skips the one of its own turn (`BOMBADIL_TURN_SNAPSHOT`). Undo of an app or of a file in the home folder in a second, without a restart, is Designed ([How Bombadil should feel, build these first](../design/ux-brief.md#build-these-first), piece 3).

### Where it breaks and what the person sees

| What goes wrong | What the person sees | Code |
|---|---|---|
| The live image | In red: `Undo starts once Bombadil is installed. The live system keeps no restore points.` The `Undo` button still shows on a changed turn, and a tap on it gives this line | `src/bombadil/launcher.py` (`_undo`) |
| Installed, but snapper or its `root` config is missing, or `snapshots = false` | In red: `Undo is off here: this system keeps no restore points.` | `src/bombadil/launcher.py` (`_undo`), `src/bombadil/snapshots.py` (`available`) |
| No turn left to undo | `Nothing to undo yet.` in red. It also answers when the older restore points have been pruned | `src/bombadil/launcher.py` (`_undo`) |
| `sudo bombadil-rollback` fails | `Could not undo: <last line of its error>`, for example `no snapshot 7` | `src/bombadil/launcher.py` (`run`, `_reason`), `iso/airootfs/usr/local/bin/bombadil-rollback` |
| The turn was stopped | No `Undo` button, because a stopped turn is not sticky. The word `undo` still works | `shell/PillState.qml` (`turn_end`), `shell/StatusLine.qml` |
| The turn changed the home folder or an app | Undo says so only after the fact: `Your home folder and apps stay as they are.` `rm` under home is marked `can’t be undone` before it runs | `src/bombadil/launcher.py`, `src/bombadil/narrate.py` (`_irreversible`) |
| `bombadil undo` in a terminal | It prints `rolled back to N; reboot to apply` and does not write the marker, so two runs in a row pick the same restore point | `bin/bombadil`, `src/bombadil/snapshots.py` (`undo_last_turn`) |
| After an undo and a restart | The restore points of the turns that were taken back remain, so the next undo can pick one of them; see [restore points](../architecture/restore-points.md) | `src/bombadil/launcher.py` (`_undo`) |
| After the restart | A new conversation. agentd keeps the conversation id and the session's model in `agentd-state.json` under the runtime directory, which survives an agentd restart but not the reboot that applies the undo | `src/bombadil/agentd.py` (`_load_state`, `_save_state`) |

## 8. Building an app and opening it

**The person wants** a small tool for a job, as a real window they can use again.

```mermaid
sequenceDiagram
    actor U as person
    participant C as provider CLI
    participant M as create_app in os-mcp
    participant H as Hyprland
    participant K as bombadil-app check
    U->>C: make me a password manager
    C->>M: create_app with title and qml
    M->>M: write ~/Apps/password-manager/
    M->>H: placement.show, the window slides in
    M->>K: check offscreen, 40 s limit, screenshot
    K-->>M: ok, errors, warnings, console, image
    M-->>C: report text and the screenshot
    C-->>U: the reply on the closing line
```

1. The line reads `On it`, then follows the `create_app` call as it streams: `Building an app`, `Building Passwords`, `Building Passwords, 2 lines`, up to `Building Passwords, 12 lines`.

   ![Building Passwords, 12 lines](../screens/pill/05-building-passwords.png)

   *The line while the scripted `create_app` call streams its QML.*

2. The app is written to `~/Apps/<name>/`, where the name is the title in kebab-case (`Password Manager` becomes `password-manager`). The window slides in from its own special workspace, `special:app-<name>`, floating and centred in a slot sized for a 540 by 660 card (the next app opens a step down and to the right, so a second never hides the first's heading), before the check has finished. A chip for the app appears above the pill: a click slides the window in or out, and `×` runs `bombadil-app close <name>`.
3. The check loads the app offscreen and gives the agent the errors with file and line, the warnings, the console output and a screenshot. The agent looks at the picture and calls `create_app` again with the complete files if something is off. An app that is already running reloads in place and keeps its saved state.
4. The turn ends with the agent's reply and `Undo` and `Details`.

   ![A Passwords window with the finished line over its foot](../screens/pill/06-passwords-made.png)

   *The scripted app, with sample entries for `alex`, and the closing line over the window's foot.*

5. Later, `passwords` or `open passwords` brings the window back (`Opened Passwords.`), `close passwords` quits it (`Closed Passwords.`) and `hide passwords` puts it away (`Put Passwords away.`). Inside the window Esc hides the app and Ctrl+W closes it. Tab completes the name ([flow 11](#11-the-launcher-words-and-completing-with-tab)).

   ![Opened Passwords](../screens/pill/10-passwords-opened.png)

   *The window back in the middle of the screen after the word `passwords`.*

### Where it breaks and what the person sees

| What goes wrong | What the person sees | Code |
|---|---|---|
| The check fails | The agent is told `<name>: check FAILED. The files are written; a running app keeps its last good version with a red banner (or shows the error list if it never loaded)` and fixes each error. A running app shows a red banner `Reload failed` that opens to the error; an app that never loaded shows `<title> could not load` with the errors | `src/bombadil/appkit/tools.py` (`report`), `src/bombadil/appkit/runtime.py` (`ERROR_VIEW`), `share/qml/Bombadil/AppWindow.qml` |
| The check does not finish in 40 s | The agent is told `the check did not finish in 40 s` and suggests an endless loop; the turn goes on | `src/bombadil/appkit/tools.py` (`run_check`, `CHECK_TIMEOUT`) |
| The title cannot become a name | The agent gets `cannot make an app name from '<title>'` and tries another | `src/bombadil/apps.py` (`slug`, `NAME_RE`) |
| The window cannot be shown | The agent gets `could not show it: <reason>`; the files are written and the check still runs | `src/bombadil/appkit/tools.py` (`create_app`) |
| The window opens late | The tool reports `started <name>; it slides in when its window opens` and the card appears when the app has mapped | `src/bombadil/appkit/placement.py` (`show`) |
| The person says `undo` | System files go back; the app stays, because it lives in `~/Apps` and home is in no restore point | `src/bombadil/launcher.py` (`_undo`) |
| `close passwords` and the app will not quit in 3 s | It is killed and the person's line is `Closed Passwords.`: `Launcher._app` counts `was killed` as done. The words `unsaved changes are lost` are in what `placement.close` returns, which only the agent's `close_app` tool sees. When it cannot be killed the line says `Could not close Passwords: …` | `src/bombadil/appkit/placement.py` (`close`), `src/bombadil/launcher.py` (`_app`) |
| The app is not on screen when asked to hide | `Passwords is not on screen.` | `src/bombadil/appkit/placement.py` (`hide`), `src/bombadil/launcher.py` (`_app`) |

## 9. The browser panel and a link from the terminal

**The person wants** to see a web page without leaving the desktop, and to have every link, from anywhere, open in the same place.

```mermaid
sequenceDiagram
    actor U as person
    participant T as a terminal or an app
    participant B as bombadil-browser
    participant A as agentd
    participant W as Chromium panel
    participant H as Hyprland
    U->>T: open a link
    T->>B: xdg-open or BROWSER with the URL
    B->>A: open_url over the socket
    A->>W: PUT /json/new on port 9222
    A->>H: show special:browser
    H-->>U: the panel slides in over the desk
```

The ways in: a link handed to `xdg-open` (`bombadil-browser.desktop` is the default handler for `http`, `https` and HTML), `$BROWSER` for every turn and for the sign-in, the agent's `show_panel` tool, a click on a picture box that names a page, Super+B ([keys](keys-and-timings.md#hyprland-binds)), and the words `browser`, `web browser`, `web`, `chrome`, `chromium`, `internet` and `google`. Whether the terminal hands a link to `xdg-open` is the terminal's own setting; this repository sets none for it.

1. `bin/bombadil-browser` repairs the argument (`example.com` becomes `https://example.com`, `localhost` becomes `http://localhost`, a path that exists becomes a `file://` address) and tells agentd.
2. agentd opens the address as a new tab through Chromium's debugging port (9222) and slides the panel in by showing `special:browser`. When Chromium is not running it starts it with that one page, in its own profile (`--user-data-dir` under the data directory, class `bombadil-browser`).

   ![The browser panel over the desk](../screens/first-boot/browser-panel.png)

   *The panel over the desk, on a search page, with the pill below it.*

3. The panel is a special workspace on top of the desk. Super+B, or the words `hide the browser`, `close the browser` and `put away the browser`, slide it back (`Put the browser away.`). The word `browser` or `open the browser` slides it in (`Opened the browser.`, or `Opened the browser, it is still starting.`). Stop leaves the browser alone ([flow 5](#5-stopping-a-turn)).

### Where it breaks and what the person sees

| What goes wrong | What the person sees | Code |
|---|---|---|
| The address is not `http`, `https` or `file` | A red line: `Not opening '<address>': only web pages and files open here.` | `src/bombadil/agentd.py` (`open_url`) |
| agentd is not running | `bombadil-browser` opens the panel itself, through `browser.open_url` | `bin/bombadil-browser` |
| Chromium is not installed | A line `Could not open the link: chromium is not installed`. Without agentd, `bombadil-browser` prints `bombadil-browser: chromium is not installed` on standard error and exits 1 | `src/bombadil/browser.py` (`open_url`), `src/bombadil/agentd.py` (`open_url`), `bin/bombadil-browser` |
| A picture box names a page and it will not open | `Could not open the page: <reason>.` This route calls `browser.open_url` directly and not agentd's `open_url` | `src/bombadil/launcher.py` (`open_thing`) |
| Chromium is starting and does not answer in 20 s | `Could not open the link: the browser is still starting` | `src/bombadil/browser.py` (`open_url`) |
| The browser is up but will not open the page | `Could not open the link: the browser would not open the page` | `src/bombadil/browser.py` (`_new_tab`, `open_url`) |
| Chromium exits as it starts | `Could not open the link: the browser closed as it started` | `src/bombadil/browser.py` (`open_url`) |
| The panel cannot slide in | The page is open and nothing is said on screen; the message `browser: could not slide the panel in` goes to standard error. Super+B shows the panel | `src/bombadil/browser.py` (`open_url`) |
| The link is the sign-in page of the provider being signed in | It becomes the sign-in's own page, with the sign-in's chips ([flow 2](#2-signing-in-to-a-provider-from-the-pill)) | `src/bombadil/agentd.py` (`open_url`), `src/bombadil/signin.py` (`browser_url`) |

## 10. Opening the details drawer

**The person wants** to see exactly what the machine ran, in order, with its output.

```mermaid
sequenceDiagram
    actor U as person
    participant P as the pill
    participant A as agentd
    participant L as Launcher
    participant D as foot drawer
    U->>P: click Details
    P->>P: hand the keyboard back
    P->>A: details, turn n
    A->>L: details, bombadil watch with the turn's log
    L->>D: foot --app-id=bombadil-details --title=Details
    L->>D: wait for the window, focus it
    D-->>U: special:details slides in
    U->>D: Esc, or any key
```

The ways in: the `Details` button on a finished line, a tap on a finished line, a click on a picture box that names a turn (the other boxes open a service, a package, a folder or a text file in the same drawer, or a page in the browser panel), and the words `history` (or `rewind`) and `wifi`. A second click on `Details` for the same turn closes the drawer.

1. The pill gives the keyboard back, because the drawer must take it, and sends `details` with the turn's number. agentd starts `foot --app-id=bombadil-details --title=Details` running `bombadil watch --file <the turn's log>`, adds `--follow` when the turn is still running, and waits for the window to open so that it can focus it.
2. The drawer slides in on `special:details` with the keyboard in it. It lists the prompt and the time, `restore point N`, each step as `▸ Installing docker` with `[system]` or `[cannot be undone]`, the exact command after `$`, the agent's reason after `why:`, the output after `│`, `│ failed (exit N)` or `│ stopped`, errors after `!`, and the closing line with its seconds. A turn that read things lists what was yours and what came from outside.

   ![The details drawer over the desk](../screens/pill/20-details.png)

   *The scripted `set up docker` turn in the drawer. The picture's last line is `Press any key to close.`; `src/bombadil/watch.py` says `Press Esc to close.` as of 2026-11-08, and any key still closes.*

3. Any key closes a turn's details and the history, also while a running turn is followed; `Esc` or `q` closes the other views ([keys](keys-and-timings.md#in-the-details-drawer)). Esc in the pill with nothing to stop or clear closes it too (`close_details`), and the same Esc puts the line away and gives the keyboard back.

   ![The drawer open with the keyboard in it](../screens/vm-fixes/drawer-open-with-keyboard.png)

   *The drawer in a virtual machine, with `Press Esc to close.` at the bottom. The stopped `sleep 300` in it reads `failed (exit -2)`: the picture was taken before the `│ stopped` word, which `src/bombadil/watch.py` prints, as of 2026-11-08, for a command that Stop ended.*

4. `history` fills the drawer with `bombadil history`: the turns by day, with the time, the prompt, the summary and `(restore point N)`; the line says `Opened the history.` `wifi` runs `nmtui connect` in the same drawer (`Opened Wi-Fi.`).

### Where it breaks and what the person sees

| What goes wrong | What the person sees | Code |
|---|---|---|
| `foot` is not installed | `Could not show the details: foot is not installed` | `src/bombadil/launcher.py` (`details`), `src/bombadil/agentd.py` (`details`) |
| The drawer's program ends before its window shows | Nothing: `Launcher.details` returns `failed` and `AgentD.details` does not read it. The other ways in say `Could not open <what>.` | `src/bombadil/agentd.py` (`details`), `src/bombadil/launcher.py` (`_view`) |
| No turn has run | The drawer prints `No turns yet.` and waits for a key | `src/bombadil/watch.py` (`show`) |
| The turn is not one of the last 50 | A click on a box that names that turn answers `The details of turn N are not kept.` | `src/bombadil/agentd.py` (`open_thing`, `turn_logs`) |
| The person presses Esc in the pill while a turn runs, or with text typed | Esc stops the turn, or clears the text, and leaves the drawer open. The drawer closes on an Esc with the field empty and nothing to stop | `shell/shell.qml`, `src/bombadil/launcher.py` (`close_details`) |
| agentd is not there | `Not connected to the agent yet.` and the drawer does not open | `shell/PillState.qml` (`details`, `_offline`) |
| A box names a path that is gone | `<name> is not there.` | `src/bombadil/launcher.py` (`open_thing`) |

## 11. The launcher words and completing with Tab

**The person wants** to open and close things by name, and to ask the machine plain questions about itself, instantly and without waiting for a model.

```mermaid
flowchart TD
    T["typed text"] --> B{"starts with !"}
    B -->|yes| S["shell command, no model"]
    B -->|no| M{"Launcher.match: an exact word"}
    M -->|yes| L["local action, answered at once"]
    M -->|no| Q["a turn for the agent"]
    L --> E["event local: start, then done"]
```

`Launcher.match` in `src/bombadil/launcher.py` decides, and it is exact: the line must be a whole word or phrase, after trimming signs and folding case. The words, the order in which `match` tries them and the answer to each are in [the launcher words](keys-and-timings.md#the-launcher-words); this flow does not repeat them. In short: `why` while a turn runs, a picture phrase, a widget question (`how's the machine`), a sign-in word, a provider verb (`use codex`), `pause` or `resume` with an AI, a core command, `use opus` or `use sonnet`, the brain's words (`brain`, `why is this here`), then, for each form of verb, the mail words, an app, a panel and a utility, and last a desk widget with a verb, in that order. Anything else goes to the agent as a turn, except `send it`, which agentd answers itself only while a draft waits ([flow 15](#15-mail)). A text in another script, or with signs in it, matches only as an app's own title.

A line that starts with `!` is never a launcher word: it runs as a shell command in a turn of its own, with no model, and its output is the closing line.

1. The person types `pass`. The rest of a name that Tab would complete, `words`, appears after it in a dimmer grey. Tab takes it. Completion starts after a couple of characters, tries apps first, then panels, widgets and commands, and never offers `restart`, `shutdown`, `lock`, `stop`, the volume words (`volume up`, `volume down`, `mute` and `unmute`) or the questions `why` and `why is this here` (`NO_COMPLETE` in `src/bombadil/launcher.py`), so a stray Tab cannot land on them.

   ![pass typed with words in a dimmer grey](../screens/pill/08-tab-ghost.png)

   *`pass` in the field with the ghost of `words`.*

2. When the text is an exact word, a small `↵ Passwords` appears at the right end of the pill. It tells the person Enter will open it and no model will be asked.

   ![passwords typed with an enter hint](../screens/pill/09-exact-hint.png)

   *`passwords` in full, with `↵ Passwords` before the clock.*

3. Enter shows `Opening Passwords` at once, then `Opened Passwords.` for a few seconds. In the scripted run the answer needed no model call (`docs/screens/pill/checks.log`).
4. `!uname -sr` shows the command's output as a single line. A `!` line also gets a restore point where snapper is configured, and it runs while the AI is not ready.

   ![A one-line answer from a shell command](../screens/pill/17-bang-command.png)

   *The output of a scripted `!` command: the kernel's name and release.*

5. `volume up` (or `louder`), `volume down` (or `quieter`), `mute` and `unmute` are utilities. `Launcher` runs `wpctl` on the default sound output, 5% a step and never past 100% (`VOLUME_STEP`), and the line reads the level, for example `Volume 55%.` (`Volume 55%, muted.` after `mute`).
6. `use opus` and `use sonnet` (also `switch to opus`, `use claude opus`, `opus please` and `back to sonnet`) choose the Claude model for the next turns of the session (`launcher.MODEL_WORDS`). agentd answers them itself, with no turn (`AgentD._switch_model`): `Using Opus from the next message. Say "use sonnet" to go back.` or `Using Sonnet from the next message.` The turn that is running finishes on the model it started with. The choice is kept in `agentd-state.json`, so an agentd restart keeps it and a reboot clears it, and each turn's log row records the model. Claude turns start on Sonnet (`config.DEFAULT_MODELS`) unless the config file names another model.

### Where it breaks and what the person sees

| What goes wrong | What the person sees | Code |
|---|---|---|
| `restart?`, `shutdown?`, `desk?` or `show machine?` | The text goes to the agent as a question; the launcher does not act on it | `src/bombadil/launcher.py` (`match`), `shell/PillState.qml` (`exact`) |
| An action fails | A red line such as `Could not open Passwords: <reason>` | `src/bombadil/launcher.py` (`run`, `failed`) |
| `volume up` with no PipeWire, or no sound output | A red line: `Sound settings need PipeWire, which is not running.` or `Could not change the volume: no sound output found.` | `src/bombadil/launcher.py` (`_volume`) |
| `use opus` while the machine is set to Codex | A red line: `Opus is a Claude model; this machine is set to use codex.` The model does not change | `src/bombadil/agentd.py` (`_switch_model`) |
| An app has the name of a utility (`Sound`) | The app wins, because apps and panels are tried before utilities | `src/bombadil/launcher.py` (`match`) |
| `restart` or `shutdown` typed in full | They run at once, with no question: `Restarting.`, `Shutting down.` | `src/bombadil/launcher.py` (`_restart`, `_shutdown`) |
| `lock` without `hyprlock` | `Nothing can lock the screen yet: hyprlock is not installed.` | `src/bombadil/launcher.py` (`_lock`) |
| An app was just made | Its name completes a few seconds later: agentd looks at `~/Apps` at a short interval and sends a new `entries` message | `src/bombadil/agentd.py` (`_watch_apps`) |
| agentd is not there | Tab still completes from the last `entries` message. Enter shows `Not connected to the agent yet.` | `shell/PillState.qml` (`completion`, `submit`) |
| A panel's app is still starting | `Opened the browser, it is still starting.` | `src/bombadil/launcher.py` (`_panel`), `src/bombadil/hypr.py` (`panel`) |

## 12. The desk being asked for

**The person wants** a card beside the pill shown or put away, or every card folded, in their own words.

```mermaid
sequenceDiagram
    actor U as person
    participant P as the pill
    participant A as agentd
    participant D as Desk
    participant M as os-mcp desk tool
    participant K as the desk in the bar
    U->>P: hide machine
    P->>A: prompt
    A->>D: Launcher._widget, apply hide machine
    D-->>A: Put Machine away. desk.toml saved
    A-->>P: event local, then a desk message
    U->>P: put watching on the right
    P->>A: prompt, not an exact word
    A->>M: the agent calls desk, move, watching, right
    M->>A: desk-tool with the turn number
    A->>D: apply move, only if asked_for_desk
    U->>K: drag a card by its title to the right rail
    K->>A: desk move, widget, rail, rank
    A->>D: apply move
    D-->>A: Moved Watching to the right rail. desk.toml saved
    A-->>K: desk message with order, rails and stripped
```

There are three ways. The exact words (`desk`, `show machine`, `hide watching`, `put watching away`) go through `Launcher` straight to `Desk.apply`, with no model. A sentence that is not an exact word goes to the agent, which may call its `desk` tool; agentd lets that call through only when the person's own typed words for that turn ask for the desk (`asked_for_desk` in `src/bombadil/desk.py`: the word `desk` or `widget`, or a verb such as `show`, `hide`, `put`, `move`, `fold` or `unfold` followed at once by a widget's name). The third is the pointer: a card can be dragged to another place or into the row beside the pill (steps 5 and 6), which sends a `desk` message to agentd, and agentd does what the words do (`Desk.apply`).

1. `desk` folds every card to a strip beside the pill (`Folded the desk.`) and says the same word again to unfold it (`Unfolded the desk.`).
2. `hide machine` answers `Put Machine away.` and `show watching` answers `Put Watching on the desk.` after it was put away, or `Watching is already on the desk.` `show machine` is also a question: it answers `Here is the machine.` (with ` It is back on the desk.` when it had been put away) and raises the Machine card for 30 s even when nothing is wrong (`vitals.ASK_FOR`, `Desk.on_ask`, `AgentD._vitals_asked`); so does `how's the machine` (`launcher.WIDGET_QUESTIONS`). A card appears only when its widget has something to say. On `main` the cards with a feed are `Now` (a plan of two steps or more, or a step that touches the system), `Watching` (while agentd lists a job) and `Machine` (agentd samples the machine, `src/bombadil/vitals.py`, and sends the `machine` message: a calm machine has no card, and a line near its limit or the question brings it). `Needs you` has a shell handler for a `dev` message (two waiting rows or more make the card) that nothing on `main` sends, and `Alive` and `Away` have neither a card nor a feed.
3. `hide needs` answers `Needs you cannot be hidden.`

   ![The desk's rails over the wallpaper](../screens/boot-and-wallpaper/headless-desk-rails-over-the-wallpaper.png)

   *A headless desk test. `Watching` on the left, the red-edged answer `Needs you cannot be hidden.` above the pill, and `Needs you` on the right. The test's driver injects the `jobs` and `dev` messages that fill the two cards (`tests/desktop/driver.py`).*

4. A sentence such as `put watching on the right` reaches the agent. Its `desk` tool sends `desk-tool` with the turn number to agentd, which checks that the turn is the running one and the person asked, then applies the move and answers `Moved Watching to the right rail.`
5. A card can be dragged. The person takes it by its title (the first 50 px of the card, the title and the line under it: the rows below start no drag, so a button or a `×` stays one) or takes a strip beside the pill, and lets go over a rail or in the row beside the pill (the bottom 64 px of the screen). `shell/DeskDrag.qml`, a left-button `DragHandler` in `shell/DeskRail.qml` and `shell/DeskStrips.qml`, reports the pointer's screen position to `DeskState.dragStart`, `dragMove` and `dragEnd`; a tap, or a slide under the drag threshold, stays a tap. While the card is in hand a 3 px line across a rail marks the gap it would take and, over the row, the card's chip shows beside the strips as an outlined ghost. A drop on a rail sends `desk` `move` with the rail and a rank, and a card dropped in the row sends `desk` `fold` with its widget. The shell moves nothing itself: the card it was taken from stays dimmed until agentd's `desk` message arrives, or for 500 ms (`settleMs`). The targets and the cases where a drag ends by itself are in [the desk](../architecture/desk.md#dragging-cards-and-folding-by-hand).
6. `fold` with a widget folds that one card to its strip by hand (`Folded Watching to its strip.`); the word `desk` is a `fold` with no widget, which agentd turns into `toggle`. A hand-folded widget stays a strip whatever the room and has no slot in its rail. It is kept as `stripped = [...]` in `desk.toml` and sent in every `desk` message. A `move` (a drop on a rail, or the tool) or a `show` gives the card its place back, and so does `unfold` with the widget. The sentence is `Unfolded Watching.` when the unfold is all that happened; a `move` that changes a place answers `Moved Watching to the right rail.` agentd does not say a drag's result on the line: it notes `at the desk: <sentence>` for the agent's next turn and writes a `local` row to `turns.jsonl`. A sentence such as `fold watching into its strip` is not a launcher word; it reaches the agent, and its `desk` tool can `fold` and `unfold` a widget too.
7. The layout is kept in `desk.toml` in the state directory (`folded`, `hidden`, `stripped`, `screen`, and each widget's rail and place), outside every restore point, so an undo never moves it.

### Where it breaks and what the person sees

| What goes wrong | What the person sees | Code |
|---|---|---|
| The agent changes the desk in a turn that did not ask | Nothing changes. The agent is told `The person did not ask for the desk in this turn, so it stays as it is. Rearrange it only when they ask.` | `src/bombadil/agentd.py` (`_desk_tool`), `src/bombadil/desk.py` (`asked_for_desk`) |
| The turn is over or is being stopped | `That turn is over, so the desk stays as it is.` goes to the agent | `src/bombadil/agentd.py` (`_desk_tool`) |
| An unknown widget or operation | `There is no widget called 'x'. The widgets are Now, Watching, Alive, Needs you, Away and Machine.` or `The desk cannot <op>. …` | `src/bombadil/desk.py` (`Desk._apply`), `src/bombadil/agentd.py` |
| `desk?` or `show machine?` | A question: it goes to the agent and the desk does not change | `src/bombadil/launcher.py` (`match`) |
| `show away` or `show alive` | `show alive` answers `Put Alive on the desk.`, because `Alive` starts put away (`OPT_IN`). `show away` answers `Away is already on the desk.` unless `Away` was put away first, and then `Put Away on the desk.` In both cases no card appears, because neither has rows on `main`. Machine is not like them: `show machine` raises its card for 30 s | `shell/DeskState.qml` (`awayModel`, `aliveModel`), `src/bombadil/desk.py` (`OPT_IN`, `ASKABLE`) |
| A drag is dropped where nothing can take it | Nothing is sent and the desk does not change: over a window or the stage (the middle of the screen), a card put back in its own place among the cards on show, a strip dropped in the row, or any drag while agentd is lost, or after a window or the room folded the card | `shell/DeskState.qml` (`_target`, `dragStart`, `dragCancel`) |
| Two coding sessions wait | The `Needs you` card and the stone's knock are designed for this, but nothing on `main` sends the `dev` message that fills them | `shell/DeskState.qml` (`_applyDev`), [desk](../architecture/desk.md) |
| A window covers a card, or the screen is narrow | The card folds to a strip, whatever the person asked ([the interaction model](README.md#the-surfaces)) | `shell/DeskState.qml` |
| `desk.toml` cannot be written | The change holds until agentd stops and is not saved, so a restart brings back the earlier layout. The reason goes to standard error | `src/bombadil/desk.py` (`save`) |

## 13. Install to disk

**The person wants** to keep this system on the computer, with undo, instead of running it from the USB stick.

On `main` this is a command typed in a terminal. The install card in the pill, with the disk drawn as a bar, one password and one red button, is Designed ([the installed OS](../architecture/installed-os.md), [Bombadil, installed](../design/installed-os-brief.md#build-these-first)).

```mermaid
sequenceDiagram
    actor U as person
    participant I as bombadil-install
    participant D as the disk
    participant C as arch-chroot
    U->>I: sudo bombadil-install DISK
    I-->>U: This erases DISK. Type the disk name to continue
    U->>I: the disk name, or --yes was given
    I->>D: sgdisk, a 1 GiB ESP, btrfs with @, @home, @snapshots
    I->>D: cp -ax of the live system, kernel to the ESP
    I->>C: user, services, snapper config root with a limit of 30, mkinitcpio, GRUB
    I->>D: copy the sign-in and the chosen AI
    I-->>U: Installed. Reboot into Bombadil.
```

1. The person opens a terminal (Super+Return) and runs `sudo bombadil-install /dev/<disk>`. The script asks `This erases /dev/<disk>. Type the disk name to continue: `; anything else exits. With `--yes` it does not ask.
2. It looks for the newest `/usr/lib/modules/*/vmlinuz` of the live system before it touches the disk (the boot medium may already be unmounted). When no such file exists, `ls` fails and `set -euo pipefail` ends the script there with `ls`'s own message and exit status 2; `No kernel found under /usr/lib/modules` and exit 1 appear only for a match that is not a regular file. It then wipes the partition table, makes a 1 GiB EFI partition and a btrfs root with the subvolumes `@`, `@home` and `@snapshots`, and prints `Copying the system…` while it copies the running live system with `cp -ax`. The kernel it found is copied to the EFI partition as `vmlinuz-linux`.
3. In a chroot it creates the machine id, the user `user` with no password, enables `greetd` and `NetworkManager`, creates the snapper config named `root` and sets it to one restore point per turn (`TIMELINE_CREATE=no`, `NUMBER_CLEANUP=yes`, `NUMBER_LIMIT=30`: no hourly points, and at most 30 numbered restore points once the cleanup timer has run), enables `snapper-cleanup.timer`, builds the initramfs and installs GRUB with a hidden menu (hold Esc at power-on to see it).
4. It copies `.claude`, `.claude.json`, `.codex` and `.config/bombadil` from the live home when they exist, so that an AI chosen and signed in on the live system carries over.
5. It prints `Installed. Reboot into Bombadil.` After the restart, the desk opens without a login screen. If the live system had chosen an AI (its `~/.config/bombadil/config.toml` was copied), the installed one starts with that choice: when the copied sign-in still holds, the pill asks nothing; when it does not, agentd starts the sign-in at once, or says there is no network ([flow 2](#2-signing-in-to-a-provider-from-the-pill)). It asks `Which AI should run this computer?` only when no choice was copied ([flow 1](#1-first-boot-and-setup)). From the first turn there is a restore point, and [flow 7](#7-undo) works.

Until the install, the pill says what is missing in its own words: `Undo starts once Bombadil is installed. The live system keeps no restore points.`

`MODE=install scripts/test-vm.sh` runs the live checks, installs to a scratch disk in a virtual machine, boots the result and tests undo across a reboot; `MODE=installed scripts/test-vm.sh` boots the disk that run left, for its second boot. [VM testing](../contributing/vm-testing.md) has the rest.

### Where it breaks and what the person sees

| What goes wrong | What the person sees | Code |
|---|---|---|
| The wrong disk is named | Nothing checks: the typed name is the only guard. The script does not look at whether the disk holds data or is the one the system is running from | `iso/airootfs/usr/local/bin/bombadil-install` |
| The disk name is not typed | The script exits with status 1 and the disk is untouched | `iso/airootfs/usr/local/bin/bombadil-install` |
| A tool fails part-way | The script stops with that tool's own message (`set -euo pipefail`). There is no cleanup: the new filesystems stay mounted under `/mnt` and the disk is half written | `iso/airootfs/usr/local/bin/bombadil-install` |
| The firmware is not UEFI | Not handled: the script installs GRUB for UEFI only (`--target=x86_64-efi`) and makes an EFI partition | `iso/airootfs/usr/local/bin/bombadil-install` |
| The installed system has no password | The user has none and `sudo` asks for none (`iso/airootfs/etc/sudoers.d/bombadil`); a password step is part of the Designed install card | `iso/airootfs/usr/local/bin/bombadil-install`, [installed OS](../architecture/installed-os.md) |
| Kernel updates and undo | The kernel and initramfs sit on the EFI partition, outside `@`, so an undo after a kernel update can pair a newer kernel with older modules | [restore points](../architecture/restore-points.md) |
| No baseline restore point at install | The first undo can go back only to the start of the first turn | [restore points](../architecture/restore-points.md) |
| The live system holds no kernel under `/usr/lib/modules` | The script stops before the disk is touched, with `ls`'s own error (`cannot access '/usr/lib/modules/*/vmlinuz'`) and exit status 2. `No kernel found under /usr/lib/modules` and exit 1 appear only for a match that is not a regular file | `iso/airootfs/usr/local/bin/bombadil-install` |
| More than 30 turns have run | When `snapper-cleanup.timer` next runs, snapper's number cleanup is configured to delete the older restore points, so undo cannot reach them. Until then more than 30 can exist, and no check on a machine has waited for a deletion; see the caveat in [restore points](../architecture/restore-points.md#retention-and-pruning) | [restore points](../architecture/restore-points.md#retention-and-pruning) |

## 14. The AI resting

**The person wants** their apps and files to carry on, and to be told plainly when asks will run again, when the AI account has run out of plan or spending, or when they have paused the AI themselves.

```mermaid
sequenceDiagram
    actor U as person
    participant P as the pill
    participant A as agentd
    participant C as the provider CLI
    participant R as rest.json
    U->>P: write a haiku about rain
    P->>A: prompt
    A->>C: the turn
    C-->>A: result, refused for the account's limit
    A->>R: set_limit, written before anything is said
    A-->>P: event rest, then turn_end requeued with the cut-off line
    A-->>P: setup resting, the ask is back at the front of the queue
    U->>P: open passwords
    P->>A: prompt, a launcher word, answered at once
    A->>R: _rest_watch compares the clock with until plus 60 s
    A-->>P: setup ready, Claude is back. Running your waiting ask.
```

Running out is a state of the machine, not an error of one ask. agentd is the only writer of the state: it holds every ask that needs a model while the state lasts, says so once with the time the provider gave, and lets everything else go on. The state is kept per provider, in `rest.json` in the state directory, so a weekly limit and a pause survive an agentd restart (`src/bombadil/rest.py`). It has three causes: `limit` (a plan window), `spend` (a spending cap or credits used up) and `hand` (paused by the person). How the stone, the chip and the field look is in [the interaction model](README.md#while-the-ai-rests); how it is built is in [the poor man switch](../architecture/poor-man-switch.md).

1. A turn is refused for the account's limit. `providers.limit()` reads the CLI's result as the account's own limit, or a CLI that waits the limit out by itself is stopped (`AgentD._refusal`, `_limit_wait`). A `!` command is never refused for a limit. agentd writes `rest.json` first, so a restart finds it (`rest.set_limit`), and sends a `rest` event with the provider, the cause, the window and the time. The turn does not end in an error: it ends with `turn_end` marked `requeued` and the cut-off line `Claude hit its limit partway, after changing 2 files. It carries on at 06:24.` (`rest.cut_off`), or the resting line itself when it changed nothing. A turn that changed nothing leaves an empty restore point, which `Undo` goes past (`skip_restore_point`). There is no receipt ([flow 16](#16-a-picture-card)).
2. The ask goes back to the front of the queue under its own number, to run again in the same conversation, and the machine rests (`_rest_turn`, `_settle`). A turn that Stop ended is not put back. What the cut-off turn changed stays changed, and `Undo` and `Details` work on it as on any turn.
3. While the AI rests the line reads `Claude is at its limit until 06:24. Your apps and files still work.` The time is a clock for today, a weekday (`Thursday 09:00`) within six days and a date beyond; with no time from the provider the line reads `Claude is at its limit.` It stays for 12 s. The stone is the hollow grey outline and still, and the empty field says `Open or find anything. Asks wait for 06:24.` A sentence that needs the agent waits as a chip labelled with the time (`06:24`), with `paused` when paused by hand, or with `limit` when there is no time. Launcher words, `!` commands, apps, background jobs and the browser panel never look at the state, so `open passwords` answers at once ([flow 11](#11-the-launcher-words-and-completing-with-tab)).

   ![The resting line, a waiting ask labelled with its time, and the field that says when asks run](../screens/resting/at-its-limit.png)

   *The scripted run refuses a turn with a real 429 from the stand-in for the API. The line reads `Claude is at its limit until 06:24. Your apps and files still work.` Under it a grey chip reads `06:24`, `write a haiku about rain` and `×`. The field reads `Open or find anything. Asks wait for 06:24.`*

4. For an ask the person typed in the pill, agentd also looks on this computer, with no model and no network, for what the sentence nearly names (`finder.find`). The line becomes `Kept for 07:27. Found on this computer:` (or `Nothing on this computer matches.`) with up to three chips under it: an app, a launcher word that only shows something, or a past ask whose steps open in `Details`. A press is `found_open`: agentd opens the thing as if its word was typed and lets go of the kept ask. The line and its chips go after 12 s, or when the ask is dropped or starts.

   ![An app and a past ask found for a kept sentence](../screens/resting/found-an-app-and-a-past-ask.png)

   *The line reads `Kept for 07:27. Found on this computer:` with two chips, `Passwords` marked `App` and `You asked: make a password manager (today)` marked `Its steps`. Two waiting chips are below them.*

5. By hand: `pause claude` or `pause the ai` rests the AI at once and `resume the ai` (or `use claude`) ends it; both are launcher words that agentd answers itself (`AgentD.rest_word`, `pause`). A click on the stone while no turn runs opens the AI card, one row per AI with a switch. The line reads `Claude is paused. Your apps and files still work.` with one button, `Resume Claude`. A pause outranks a limit, since pressing the switch back on should show what is left, and it is kept in `rest.json` until it is ended.

   ![Paused by hand, with one Resume button](../screens/resting/paused-by-hand.png)

   *The line reads `Claude is paused. Your apps and files still work.` Under it a button reads `Resume Claude`, and a chip reads `paused`, `write a haiku about rain` and `×`.*

6. A limit with a time has no button, because it lifts by itself. A spending limit, or a limit with no time, shows `Raise the limit`: it opens the provider's own usage page in the browser panel (nothing is bought here) and then reads `Try again`. `Try again` clears the limit and says `Trying Claude again. Running your 2 waiting asks.`
7. The limit lifts on the wall clock. `_rest_watch` compares the clock with the provider's time plus a 60 s grace every 60 s (`REST_POLL`), so a computer that was asleep through it wakes right, and it asks the provider nothing. The line says `Claude is back. Running your 2 waiting asks.` The ask that was cut off runs first, told what it had already changed (`_resume_notes`), and the first waiting ask is the check: if the account is still out, it is refused again and the machine rests again with the time of that refusal.

`bombadil ask` does not wait for the reset. It prints the resting line on standard error and exits 75 (`EX_TEMPFAIL`); the ask stays queued in agentd.

### Where it breaks and what the person sees

| What goes wrong | What the person sees | Code |
|---|---|---|
| The result names a limit that `providers.limit()` does not recognise as the account's | The turn ends with the error line `This account has hit a usage or spending limit. Try again after it resets, or raise the limit.` and the machine does not rest ([flow 3](#3-asking-for-something)) | `src/bombadil/agentd.py` (`_limit_text`), `src/bombadil/providers.py` (`limit`) |
| The provider's time is already past (a clock that is off, or a window that rolled over) | The time is not kept. The line gives a time five minutes ahead (`RETRY_AFTER_REFUSAL`, 300 s) and the first ask after it, and a minute of grace, is the next try | `src/bombadil/rest.py` (`resolve`) |
| Stop is pressed as the limit hits | Stop wins: the ask is not put back. The limit is still remembered and the machine rests | `src/bombadil/agentd.py` (`turn`, `_rest_turn`) |
| An app's ask has to wait | It replaces that app's older waiting ask, which goes with no line: at most one chip per app, so an app on a timer cannot fill the queue | `src/bombadil/agentd.py` (`_replace_app_ask`) |
| agentd restarts while the AI rests | The restarted agentd reads `rest.json` and rests at once, with the same time or pause. Its queue is in memory, so the kept asks are gone and have to be typed again | `src/bombadil/agentd.py` (`_settle`), `src/bombadil/rest.py` |
| A sign-in ends | The login may be for another account, so its limit is cleared and the first ask is the check. A pause by hand stays | `src/bombadil/agentd.py` (`_run_signin`, `_watch_external`) |
| A press on `Raise the limit` finds the page will not open | `Could not open the link: <reason>`, and the button stays `Raise the limit` | `src/bombadil/agentd.py` (`rest_action`) |
| `resume` while the AI is not paused | `Claude was not paused.` Or, when its limit is still on, the resting line | `src/bombadil/agentd.py` (`pause`) |
| The AI is not signed in or agentd is not there | The setup lines of [flow 2](#2-signing-in-to-a-provider-from-the-pill) and the offline line of [flow 3](#3-asking-for-something) come first; resting is only for a provider that can be asked | `src/bombadil/agentd.py` (`_describe`, `_settle`) |

## 15. Mail

**The person wants** to hear that mail from someone they know has come, to answer it or have the agent draft the answer, and to be the one who presses Send.

```mermaid
sequenceDiagram
    actor U as person
    participant P as the pill
    participant A as agentd
    participant S as bombadil-mail
    participant W as the Mail window
    S-->>A: push new_mail, from someone known
    A-->>P: notice, Priya Shah: Launch date by noon?, with Reply and Open
    U->>P: press Reply
    P->>A: notice_action, reply
    A->>S: draft, reply_to the mail, created_by person
    A->>W: show the reply box
    U->>W: press Send
    W->>A: press, with the draft id and the fingerprint
    A->>S: send, once
    S-->>A: receipt
    A-->>P: notice, the receipt, with Open in Gmail
```

The Mail window, the service that keeps the mail and Thunderbird as the unseen engine are described in [Mail](../architecture/mail.md). Mail is partly shipped: one window and the service are on `main`, tested on a fake engine and, where one is installed, on a real Thunderbird against a local mail server. No real Gmail, Outlook or iCloud account has been tried, so the notices below are what the code does, not what a real inbox has been seen to do. With no mail service agentd costs one failed connect every five seconds and says nothing.

1. agentd subscribes to the service over `mail.sock` (`mail/watch.py`, `Watch`). When mail comes, `Says.new_mail` says it only when the sender is known to the person, the mail is unread and in the inbox, and it was not said before. A stranger's mail waits in the Mail window. The notice is one line above the pill, `Priya Shah: Launch date by noon?`, with the chips `Reply` and `Open`, and it goes after 300 s. It is a notice, not the pill's own line: agentd keeps one stack of at most four, newest first, each with at most three chips, and the shell draws it (`src/bombadil/notices.py`, `shell/NoticeChips.qml`). What a sender wrote is cleaned to one line first (no control or direction-flipping characters; the name cut at 40 characters and the subject at 120).
2. A press on a chip is `notice_action`. agentd first checks that the client is the person's and not a process of an agent's turn (`_persons`). The handler runs once: a second press while it runs does nothing. `Open` shows that mail in the Mail window. `Reply` asks the service for a draft with `reply_to` the mail and `created_by` `person`, and brings the window in on the reply box. The notice ends when its handler is done.
3. The agent can draft too, with its `mail_draft` tool (`src/bombadil/mail/tools.py`). It has no tool that sends. agentd says `Reply to Priya is ready. Sending is yours.` as a notice with `Open`, which stays until the draft is sent or put away, and the Mail window slides in on it. The first time this ever happens the line adds `I never press Send for you. Change anything in it first if you like.` (kept in `told.json`, so it is said once on a computer). The reply box shows the warnings the service found, such as an address nobody typed.

   ![A reply written by Bombadil in the Mail window](../mail/screens/mail-reply.png)

   *The window on the fake engine, so every person and address in it is invented. The open mail is `Launch date by noon?` with a reply box under it, `Written by Bombadil`, a Cc address with an amber warning that starts `New address:` and says the person did not type it, and the `Send` button with an orange ring and `Yours: Send` beside it.*

4. `send it` typed alone is not a send. While a draft waits (agentd asks the service), `send`, `send it`, `send that` and `send this`, with `yes`, `yeah`, `ok`, `okay`, `please` or `go ahead and` before or `now` or `please` after, are answered by agentd with no model: the window comes in on Drafts and the line reads `Sending is yours. It's under the pointer.` With no draft waiting, or with a question mark, the words go to the agent like any other sentence ([flow 11](#11-the-launcher-words-and-completing-with-tab)).
5. The person presses `Send`. The button is armed 350 ms after it can be pressed (`ARM_MS`), and 1800 ms when the draft in the box was changed by somebody else just before (`ARM_CHANGED_MS`, the case after an agent's edit), so a click on its way elsewhere is not a press. The window sends `press` with the draft's id and fingerprint to agentd. `Outbox.press` refuses a sender it cannot name and a process inside an agent's turn, writes one row to `presses.jsonl` (never any mail text), and asks the service to send, once, within 90 seconds. The service checks that the fingerprint it holds is the one the window drew, writes `sending` before it asks the engine, and answers with a receipt ([Mail, a send](../architecture/mail.md#a-send)).
6. The receipt is a notice, `Sent.` or the service's own line, in the `done` tone for 120 s, with an `Open in Gmail` chip when the provider has a web page. The `is ready` notice is taken away. The agent's next turn is told `the person pressed Send: ...`.
7. The words `mail`, `email`, `inbox`, `my mail` and the like open the Mail window with no model, ahead of the person's own apps ([keys and timings](keys-and-timings.md#the-launcher-words)).

### Where it breaks and what the person sees

| What goes wrong | What the person sees | Code |
|---|---|---|
| The mail service is not running | No notice comes, because nothing is pushed. A press on `Reply` turns its notice red with `Mail is not running yet.` for 20 s, and a press on `Send` answers `Mail is not running yet. Nothing was sent.` | `src/bombadil/mail/watch.py` (`Says.new_mail`, `DOWN`), `src/bombadil/outbox.py` (`MAIL_DOWN`) |
| Mail from a stranger, or mail already read, or not in the inbox | No notice. It waits in the Mail window | `src/bombadil/mail/watch.py` (`Says.new_mail`) |
| A press on `Send` comes from inside an agent's turn (a window the agent opened) | `That press came from inside an agent's turn, and sending is yours. If this is your own window, close it and open it again from the pill. Nothing was sent.` | `src/bombadil/outbox.py` (`Outbox._press`) |
| The service takes a send and does not say whether it went | A red notice that stays: `I can't tell whether that went. Look in Sent before you press Send again.` Nothing retries it; only the person's own second press, after looking in Sent, can | `src/bombadil/outbox.py` (`UNKNOWN_LINE`), `src/bombadil/mail/watch.py` (`Says.pressed`) |
| A draft is sent and nobody pressed Send here | The receipt says so: `That was not your press on Send.`, in the red tone, and it stays | `src/bombadil/mail/watch.py` (`Says.sent`) |
| A chip's handler fails | The notice turns red, `Could not do that: <reason>`, for 30 s | `src/bombadil/agentd.py` (`_notice_action`) |
| An app of the person's called `mail` stands in the apps directory | The real window will not open, so that its Send is never the one an app drew: the word `mail` answers `Could not open Mail: an app of yours called mail stands in front of Mail; rename it to open Mail`, and a chip that shows the window turns its notice red with `Could not do that: ...` and the same reason | `src/bombadil/launcher.py` (`MAIL_SHADOWED`, `open_mail_window`), `src/bombadil/agentd.py` (`_notice_action`) |
| Five mails come in a burst | The stack holds four. The oldest notice that is not red makes room, so a send nobody is sure of is not pushed off | `src/bombadil/notices.py` (`Notices.post`) |
| `send it` with no draft waiting | It goes to the agent as a sentence | `src/bombadil/agentd.py` (`handle`), `src/bombadil/launcher.py` (`is_send_word`) |

## 16. A picture card

**The person wants** a picture of something about the machine, drawn from the machine and not from memory, and to open what is in it.

```mermaid
sequenceDiagram
    actor U as person
    participant P as the pill
    participant A as agentd
    participant M as sysmap
    participant K as the card in the bar
    U->>P: how am I connected
    P->>A: prompt, a picture phrase
    A-->>P: event local, start, Drawing how you're connected
    A->>M: capture network, no model
    M-->>A: a diagram card
    A-->>K: event card
    A-->>P: event local, done, Showing how you're connected.
    U->>K: click the NetworkManager box
    K->>A: open, unit, NetworkManager.service
    A-->>P: event local, Showing NetworkManager.service.
```

A picture is a `diagram` card: a shape (`chain`, `layers`, `compare` or `timeline`), up to twelve boxes and the links between them, made from data and laid out the same way every time. The shapes, the pictures the machine draws from itself and how they are made are in [cards and pictures](../architecture/cards-and-pictures.md). A picture comes by three doors.

1. A picture phrase typed in the pill, with no model. It must be the whole sentence, exactly (`how am I connected`, `what starts when I boot`, `where did my disk go`, `what's playing where`, `my screens`, and the like), and `what does NetworkManager need` draws one service. agentd answers with a `local` event that starts as `Drawing how you're connected`, reads the machine (`sysmap.capture`, half a second for most of them), sends the card as a `card` event and ends the `local` event with `Showing how you're connected.` ([flow 11](#11-the-launcher-words-and-completing-with-tab)). Like every launcher word it works while the AI rests ([flow 14](#14-the-ai-resting)).
2. The agent's tools. A longer or different sentence goes to the agent, which has `system_map` for the same pictures (a subject, a pointer and one line from the agent; the facts are the machine's) and `show_card` for a diagram it draws itself. agentd checks every card again (`cards.accept`) and answers `card_ack`, whether it was shown. For Claude a `show_card` call is drawn as it is written: the frame and the first boxes appear with `drawing…` where the source is, and the whole card swaps in place.
3. A receipt. After a turn that changed the network, the disks, the sound, the screens or a service, agentd compares the part as it was before the turn with how it is after, and when something changed it draws that, with `receipt` set, just after the closing line ([flow 3](#3-asking-for-something)).

In the bar (`shell/CardHost.qml`, `shell/PillState.qml`) there is one picture at a time and a newer one replaces it. It shows a title, `from this machine` or `drawn by the agent`, a `×`, the picture (it scrolls when it is taller than the card allows) and the card's one sentence, three lines at most. A picture that was asked for keeps the line that came with it and stays until Esc, the `×` or the next turn. A receipt was not asked for: it goes with its line, after 15 s, and resting the pointer on it keeps both. A window opened on the stage (an app, a panel, the Brain) takes the picture away, unless it was drawn in the last 2.5 s or a box in it was clicked in the last 5 s. A full-screen window hides it until the window is gone.

A box that names something opens it, with `open` and a kind: `path` (a folder or a text file in the drawer, any other file with its app), `unit` and `package` (their status in the drawer), `url` (the browser panel, [flow 9](#9-the-browser-panel-and-a-link-from-the-terminal)) and `turn` (that turn's steps, as in [flow 10](#10-opening-the-details-drawer)). agentd checks the target again before it acts on it (`cards.check_opens`).

### Where it breaks and what the person sees

| What goes wrong | What the person sees | Code |
|---|---|---|
| The machine cannot be read (no PipeWire, Hyprland not answering) | A red line with the reason in one sentence, such as `Could not read the sound: PipeWire did not answer.` An earlier picture is put away, so the error is not read as being about it | `src/bombadil/sysmap.py` (`Unavailable`), `src/bombadil/agentd.py` (`picture`), `shell/PillState.qml` |
| The agent's card is not valid | Nothing is drawn. The agent is told what to fix (`card_ack` with `shown` false and the errors) and tries again | `src/bombadil/cards.py` (`accept`), `src/bombadil/agentd.py` (`_card_message`) |
| No bar is connected | Nothing is drawn, and the agent is told so (`card_ack` with `shown` false) | `src/bombadil/agentd.py` (`_card_message`) |
| agentd is lost while a picture is half drawn | The half-drawn picture is put away, since it will not be finished. A whole picture stays | `shell/PillState.qml` (the lost handler) |
| A `show_card` call fails or never finishes | The half-drawn picture is taken back (`gone`) | `src/bombadil/agentd.py` (`_clear_streams`) |
| A box names a file that is gone, a turn that is not kept, a page that will not open or a kind with nothing to open it | `<name> is not there.`, `The details of turn N are not kept.`, `Could not open the page: <reason>.` or `Nothing here opens <name>.` | `src/bombadil/launcher.py` (`open_thing`), `src/bombadil/agentd.py` (`open_thing`) |
| A box is clicked while agentd is not there | Nothing is sent. The line flashes `Not connected to the agent yet.` for a moment | `shell/PillState.qml` (`openThing`) |
| A turn changed nothing, was a `!` command, or the agent drew its own picture | No receipt | `src/bombadil/agentd.py` (`_receipt`, `turn`) |
| `explain` is `brief` in the config file | No receipts. Picture words and the agent's pictures still work | `src/bombadil/agentd.py` (`turn`), `src/bombadil/config.py` |
| Another turn starts before the receipt is ready | No receipt: it is drawn only if no turn runs and the closed turn is the one it is for | `src/bombadil/agentd.py` (`_receipt`) |

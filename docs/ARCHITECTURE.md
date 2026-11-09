# Architecture

> **Status:** Partly shipped
> **Code:** `bin/`, `src/bombadil/`, `shell/`, `share/`, `iso/`
> **Design:** [Design briefs](design/README.md), [Principles](principles.md)
> **Verified:** 2026-11-09 against `main` at `ca7f80a`: the turn below, the diagram of the parts and the desk and coding-sessions rows were read again in `src/bombadil/agentd.py` (`handle`, `_greet`, `_dev_broadcast`, `dev_action`, and every change since `9d5c729`), `src/bombadil/launcher.py` (`_dev_match`, and that no word starts `tell`, `give`, `review` or `ship`), `src/bombadil/dev.py` (`_start`, `_checkout_taken`, `_make_copy`, `_bare`, `session_env`), `src/bombadil/away.py`, `src/bombadil/desk.py` (`WIDGETS`, `ASKABLE`), `bin/bombadil`, `bin/bombadil-signal`, `share/zellij/config.kdl`, `shell/shell.qml` (where the chips row sits and what the bar's zone counts), `shell/DevState.qml` and `shell/DeskState.qml`; the code table against the folders `bin/`, `share/`, `iso/`, `scripts/` and `tests/`, the docstring of `bin/bombadil-mail-host` and the user units and `hyprland.lua` under `iso/airootfs/etc/`; the Status column against the Status block of each page in `architecture/`, and the wording of each row of the parts table against the opening of its page. Read on 2026-11-08 at `9d5c729` and not again, because `git diff 9d5c729 ca7f80a` shows no change in them: `src/bombadil/config.py`, `src/bombadil/snapshots.py`, `src/bombadil/narrate.py`, `src/bombadil/mcp_server.py`, `shell/StatusLine.qml` and `shell/PillState.qml`; `src/bombadil/providers.py` changed only in its limit code, which no sentence here depends on. The foundation table was checked against [the foundation brief](design/foundation-choices.md) and the dates in [the decision log](decisions/foundation-and-stack.md) on 2026-11-08, and its agent-runtime row against the brief again on 2026-11-09. What changed between `ca7f80a` and `f0485c2` was added and checked against `f0485c2`: the voice and tips rows with their Status and the shell row's welcome line and name card (against the openings of their pages), the label on the socket in the diagram of the parts and the paragraph on the voice under it (`src/bombadil/voice.py`), the system prompt in step 3 of the turn (`system_prompt` in `src/bombadil/providers.py` and `note` in `src/bombadil/persona.py`), and the rows of the code table for `src/bombadil/`, `shell/`, `share/qml/Bombadil/`, `share/voice/` and `share/tips/`. The rest of the page was not read again.

Bombadil is a Linux distribution whose interface is an AI agent. The person types in one pill at the bottom of the screen; the agent, one of the vendors' own command-line programs running in full-access mode, does the work; and every step it takes is shown on screen. On an installed system a restore point is taken before each turn, so the latest turns can be taken back at the next boot; a step marked "can't be undone" is not covered ([restore points](architecture/restore-points.md)). This page is the map: the parts, the path of one turn through them, where the code lives and how far each piece is built. Each part has a page of its own in [`architecture/`](architecture/), with its interfaces, where its state lives and how to extend it.

## The parts

```mermaid
flowchart LR
    person(["the person"]) --> shell["shell: pill, line, cards, desk, session dots"]
    shell <-->|"JSON lines on a Unix socket, the dev message and the welcome line among them"| agentd["agentd: one turn at a time"]
    agentd -->|"before each turn, when snapper is set up"| snap["snapper: a restore point"]
    agentd -->|"runs, full access"| cli["provider CLI: claude or codex"]
    cli <-->|"MCP over stdio"| mcp["bombadil-os-mcp: the OS as tools"]
    mcp --> hypr["Hyprland: panels and windows"]
    mcp --> apps["kit apps in ~/Apps"]
    mcp -.->|"cards, jobs, desk, mail"| agentd
    agentd -.->|"turn notes"| brain["brain: the index of what happened"]
    agentd -.->|"mail tools, notices"| mail["mail service: Thunderbird, unseen"]
    agentd -->|"keeps the registry, starts each session"| dev["coding sessions: dev.py"]
    dev -->|"zellij, in its own systemd scope"| tools["claude, codex or a shell"]
    tools -.->|"hooks run bombadil-signal: dev-signal"| agentd
```

| Piece | What it is | Status | Page |
|---|---|---|---|
| agentd | The session daemon: one socket, one turn at a time, the queue, Stop, the turn log | Shipped | [agentd](architecture/agentd.md) |
| os-mcp | The MCP server both CLIs load: the OS as typed tools | Shipped | [os-mcp](architecture/os-mcp.md) |
| shell | The Quickshell bar: the pill, the line above it (agentd's welcome shows there), the name card, the stone, the chips (queued asks, setup choices, found things, notices, running apps, coding sessions), the wallpaper | Partly shipped | [shell](architecture/shell.md) |
| desk | Cards in two rails and strips beside the pill: Now (the route of a turn), Watching (jobs), Needs you (the coding sessions), Machine (the machine's readings) and Away (the turn log and the sessions, shown when asked); a card can be dragged to a rail or into the row, and a strip to a rail | Partly shipped | [desk](architecture/desk.md) |
| app kit | `import Bombadil`: native apps the agent writes, with a runtime, a checker and a skill | Partly shipped | [app kit](architecture/app-kit.md) |
| browser and sign-in | Chromium as a slide-in panel, and provider sign-in that happens in the pill | Partly shipped | [browser and sign-in](architecture/browser-and-signin.md) |
| cards and pictures | Validated diagrams the agent and the machine draw above the pill, and the reasons behind each step | Partly shipped | [cards and pictures](architecture/cards-and-pictures.md) |
| restore points | A btrfs snapshot before each turn on an installed system, and "undo" | Partly shipped | [restore points](architecture/restore-points.md) |
| ISO and install | The archiso image, the installer, the boot and the console | Shipped | [ISO and install](architecture/iso-and-install.md) |
| session services | The user units that put the daemon, the bar and notifications back when they die, and the key that restarts the bar | Partly shipped | [session services](architecture/session-services.md) |
| brain | An index that writes itself from what the machine saw, and the Focus window | Partly shipped | [brain](architecture/brain.md) |
| coding sessions | Named sessions of the vendors' coding tools, or a shell, that outlive their window and come back when the name is typed, with a dot each in a row above the pill's left end. A second Claude or Codex session on a git repository gets its own `git worktree`. The rest of the fence (no `sudo`, ports and limits), undo per session, handing words across (`tell`, `give`, `review`) and shipping are designed | Partly shipped | [coding sessions](architecture/coding-sessions.md) |
| voice | A name for the person, three voices, and one true line when they arrive or leave, built from local templates with no model; the words of empty places are in the table and only `bombadil history` reads them | Partly shipped | [voice](architecture/voice.md) |
| tips | The data side of Tips: twenty one-line tips of what to ask, checked against what the machine can back, with what the person has tried kept by agentd, which sends a `tips` message that no client draws | Partly shipped | [tips](architecture/tips.md) |
| self-improvement loop | Noticing repeated asks and checking itself | In progress | [loop](architecture/loop.md) |
| mail | Mail in Bombadil's own view, with Thunderbird as the unseen engine | Partly shipped | [mail](architecture/mail.md) |
| poor man switch | What the machine does when the AI's plan runs out | Partly shipped | [poor man switch](architecture/poor-man-switch.md) |
| installed OS | Bombadil installed on a disk. On `main`: the plain installer, the kernel rule and a limit of 30 restore points. The encrypted layout is on an unmerged branch; packages and updates are designed | Partly shipped | [installed OS](architecture/installed-os.md) |

Coding sessions run beside the turn, not through it: agentd starts the vendor's tool in a zellij session of its own, the tool's hooks tell agentd what it is doing (`bombadil-signal`), and the shell draws the dots from the `dev` message ([coding sessions](architecture/coding-sessions.md)).

The voice runs beside the turn too: the bar says hello to agentd (`bar`), and agentd answers with the name card the first time and with one welcome line after that, dropped and never queued while a turn runs; a shutdown's start text says goodbye ([the voice](architecture/voice.md)).

[The roadmap](roadmap.md) says where each piece stands and what is left. [The glossary](glossary.md) explains the project's own words.

## One turn

```mermaid
sequenceDiagram
    participant P as the person
    participant S as shell
    participant A as agentd
    participant R as snapper
    participant C as provider CLI
    participant M as os-mcp
    P->>S: types a line and presses Enter
    S->>A: prompt
    A-->>S: turn started (before the restore point is saved)
    opt snapper is set up
        A->>R: create a restore point
    end
    A->>C: run one turn, os-mcp attached
    C->>M: tool calls
    M-->>C: results
    C-->>A: a stream of JSON events
    A-->>S: text, tool and result events for every client
    A->>A: append the turn to turns.jsonl
    S-->>P: the answer and, when the turn changed something, Undo and sometimes a receipt
```

1. The shell (or `bombadil ask`) writes a `prompt` to the agentd socket. Words that need no model (`undo`, `history`, `open ...`, the picture words, `pause claude`, a coding session's name such as `claude acme` or `end reviewer`, and the like) are matched first by the launcher and answered by agentd itself, so they never reach the provider. `stop` is a message of its own and works while a turn runs. A line that starts with `!` is a turn whose "provider" is the shell: it runs the command and shows what it printed.
2. agentd runs one turn at a time and queues the rest, and it holds asks that need the AI while the AI cannot be asked (not signed in, offline or resting). Before the provider starts it asks snapper for a restore point when snapper is set up (an installed system, not the live image, and not with `snapshots = false`); a snapper that exits with an error costs the turn its restore point and nothing else. An installed system keeps about the newest 30 restore points, and an undo applies at the next boot ([restore points](architecture/restore-points.md)).
3. For the turn it starts the provider's CLI as a fresh process, in full-access mode, with `bombadil-os-mcp` in its tool configuration and Bombadil's system prompt appended (it carries the person's name and voice once they have chosen), and resumes the saved conversation (`session_id`), so the machine has one conversation. Claude is asked for Sonnet unless the config names a model or the session said `use opus` (a launcher word, answered without a turn), and the conversation and that choice survive a restart of `agentd`, not a reboot ([models](architecture/agentd.md#models)).
4. The CLI's stream becomes events, broadcast to every connected client. The shell turns them into the line above the pill, the stone's face, cards and the desk. The OS tools act on Hyprland, on apps, on restore points and on mail, and ask agentd for what it owns (cards, jobs, the desk, the mail view).
5. The turn is appended to `~/.local/state/bombadil/turns.jsonl` and the brain is told, never waited for.

The details, exact message names and the edge cases (a stopped turn, a missing provider, a sign-in that expires mid-turn) are in [agentd](architecture/agentd.md), and how each step reaches the screen is in [the UX flows](ux/flows.md).

## Where the code lives

| Path | What is there |
|---|---|
| `bin/` | The commands: `agentd`, `bombadil`, `bombadil-os-mcp`, `bombadil-app`, `bombadil-browser`, `bombadil-shell`, `bombadil-signal` (what a coding tool's hooks run, so the dots know its state), the brain's two services (`bombadil-brain`, `bombadil-brain-watch`), Mail's service (`bombadil-mail`) and the native-messaging host that Thunderbird starts for it (`bombadil-mail-host`) |
| `src/bombadil/` | The Python behind them, among it `dev.py` (the coding sessions), `away.py` (the Away card), `voice.py`, `greet.py` and `persona.py` (the voice) and `tips.py` (Tips). Standard library only, apart from the app kit's `PySide6` and `cryptography` |
| `shell/` | The Quickshell bar in QML, among it the coding sessions' dots (`DevState.qml`, `SessionChips.qml`, `SessionPeek.qml`) and the name card (`PersonaCard.qml`) |
| `share/qml/Bombadil/` | The app kit and the design tokens (`Theme.qml`, with the one switch for reduced motion) the shell and every app read |
| `share/skills/bombadil-apps/`, `share/app-template/`, `share/apps/` | The skill that teaches the CLIs to build apps, the template they start from and the built-in apps (the Brain and Mail windows) |
| `share/skills/bombadil-mail/`, `share/mail/` | The skill for the mail tools and the Thunderbird add-on that Mail's engine loads |
| `share/wallpaper/`, `share/grub/` | The wallpaper picture the bar draws, and the files of a GRUB theme that nothing in the tree on `main` installs |
| `share/voice/`, `share/tips/` | The words Bombadil says when the person arrives or leaves (`lines.toml`) and the table of tips (`tips.toml`) |
| `share/zellij/` | zellij's settings for the coding sessions' invisible terminal (`config.kdl`: no keys of its own, no frames, no tips; `dev.py` closes zellij's tab and status bars when it starts a session) |
| `iso/` | The archiso profile: packages (`zellij` among them), the live session, the installer, units, the managed settings that make Claude Code and Codex run `bombadil-signal`, the boot menu |
| `scripts/` | Building the ISO, running it in a VM and testing it there (`test-vm.sh`), a development session, the VM tools |
| `tests/` | The unit tests, the QML tests, the headless desktop test (`tests/desktop/`), the btrfs kernel VM harness (`tests/vm/`) and the mail lab (`tests/mail/`). The ISO smoke is `bombadil-smoke` in `iso/`, run by `scripts/test-vm.sh` |
| `docs/` | Everything you are reading, with [an index](README.md) |

The full map with the conventions the code shows is in [the development guide](contributing/development.md), and every name the system exposes (commands, environment variables, files, units, ports) is in [the reference](reference.md).

## The foundation

These choices were made on 2026-09-27 and are the ground the pieces stand on. The reasons are in [the foundation brief](design/foundation-choices.md) and the full list of decisions is [the decision log](decisions/README.md).

| Choice | Pick | Why |
|---|---|---|
| Base | Arch with btrfs and snapper | Agents know it; a rolling graphics stack; archiso; snapshots are the undo |
| Display | Hyprland first, a custom compositor later (an idea, no code) | Slide-in special workspaces and IPC out of the box; a browser needs a real Wayland server |
| First target | A VM, with an ISO meant to boot hardware too (no boot on hardware is recorded) | A fast loop while the core changes daily |
| Agent runtime | The official Claude Code and Codex CLIs, wrapped by `agentd` | The person's own Claude or ChatGPT login is used; the vendors' tool use is not rebuilt |
| Generated apps | QML through PySide6, in `~/Apps/<name>/` | No build step, hot reload, real windows, no ports |
| Provider switching | One MCP server, `bombadil-os-mcp` | The same OS abilities whichever provider runs |
| Browser | Chromium with remote debugging on | The panel is a real browser the person can watch |

## What every piece keeps

[The principles](principles.md) are the rules the design keeps, and each piece page says which ones it leans on. Four decide most questions:

- **The person is a passenger.** The machine does the work and shows what it did, in plain words and pictures ([the principle](principles.md#the-person-is-a-passenger)).
- **Full access, with undo instead of guard rails.** Nothing asks permission for what a restore point can reverse ([the principle](principles.md#full-access-with-undo)).
- **Something true is on screen within 200 ms.** Nothing waits on a model for what the machine already knows ([the principle](principles.md#something-true-in-200-ms)).
- **Every piece degrades.** A missing part costs its own feature, never the bar ([the principle](principles.md#degrade-and-recover)).

Before you change a piece, read its page and run [the checklist](principles.md#checking-a-change-against-the-principles). What a change must also update is in [documenting your piece](contributing/documenting.md).

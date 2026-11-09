# Restore points

> **Status:** Partly shipped. On `main`: one root restore point per turn, pruned to the newest 30 by snapper's number cleanup with no hourly points, the `undo` word and button, the `rollback` tool, `bombadil undo`, `bombadil-rollback`, and the installer layout that makes the swap possible. Not built: the pair design with 100 changed pairs kept, pruning of `@.undone-*`, restore points of `/home` or of the kernel, undo that applies at once, and the installed-OS disk layout. Each is marked designed below.
> **Code:** `src/bombadil/snapshots.py`, `iso/airootfs/usr/local/bin/bombadil-rollback`, `iso/airootfs/usr/local/bin/bombadil-install`, `src/bombadil/launcher.py`, `src/bombadil/mcp_server.py`, `src/bombadil/agentd.py`, `src/bombadil/config.py`, `src/bombadil/paths.py`, `src/bombadil/rest.py`, `bin/bombadil`, `src/bombadil/narrate.py`, `src/bombadil/watch.py`, `src/bombadil/providers.py`, `shell/PillState.qml`, `shell/StatusLine.qml`, `shell/DeskState.qml`, `iso/profiledef.sh`, `iso/airootfs/usr/local/bin/bombadil-setup`, `iso/airootfs/usr/local/bin/bombadil-smoke`, `scripts/vm-tools/update-in-place`, `scripts/test-vm.sh`, `src/bombadil/brain/ingest.py`, `src/bombadil/brain/witnesses.py`, `iso/packages.x86_64`, `iso/airootfs/etc/bombadil/config.toml`, `iso/airootfs/etc/sudoers.d/bombadil`
> **Design:** [UX brief, rule 4](../design/ux-brief.md#the-rules), [Foundation choices, section 1](../design/foundation-choices.md#1-base-distro--arch), [Installed OS brief, rule 7 and piece 1](../design/installed-os-brief.md#the-rules)
> **Verified:** 2026-11-08 against `main` at `7176e55` The line numbers it cites were moved to the code at `ca7f80a` afterwards; the text was not read again.

A restore point is a btrfs snapshot of the root subvolume that `agentd` takes through snapper before each turn runs. Saying `undo` makes the newest one the root again, and the swap applies at the next boot, not at once. This is what stands in for permission prompts: the agent has full access, and the person can go back.

## How it works

The person mostly sees "restore point" (`agentd` says "Saving a restore point"); the one line for a snapper failure says "undo point" (`agentd.py:2101`). The code, the snapper commands and the os-mcp tools say "snapshot". Other functions named `snapshot` (`Desk.snapshot`, `Jobs.snapshot`, `sysmap.snapshot`) return state tables and have nothing to do with this piece.

### What a restore point is

- One snapper snapshot in the snapper config `root`, made by `Snapshots.create` with `sudo snapper -c root --jsonout create --print-number --cleanup-algorithm number --description <text>` (`src/bombadil/snapshots.py:36-47`, the call is at 45-46). It is taken before the turn, and none is taken after it. Every restore point made through `Snapshots.create`, the turn's and the one the agent's `snapshot` tool takes, is therefore one that snapper's number cleanup may delete (see Retention and pruning).
- Its description is `turn:<n>: <first 60 characters of the prompt>`, for example `turn:4: install htop` (`src/bombadil/agentd.py:2283`). A `!` line keeps its `!`. `<n>` is `AgentD.turns`. At start it is the larger of the number of the last turn in `turns.jsonl` (older rows without a number count in order) and the number in the `turn` file of the state folder, which each turn writes before snapper runs (`agentd.py:281,2080-2082,2616-2663`). Numbers therefore go on across restarts and across an undo, also past a turn that was cut off before it wrote its row. Undo never reads `<n>`: it uses snapper's own number and the `turn:` prefix.
- It is taken after `turn_start` has gone out and the number is taken, and before the provider CLI starts, for every turn that gets that far: model turns and `!` lines alike (`agentd.py:2072-2103`). The line above the pill says "Saving a restore point" while snapper works, and "Waiting for Claude" (or Codex) once the CLI is up (`agentd.py:2097,2158`).
- Its number goes out as a `snapshot` event, into the turn's row in `turns.jsonl` as `snapshot`, and into the CLI's environment as `BOMBADIL_TURN_SNAPSHOT` (`agentd.py:2103,2134-2137,2410`).

The rest of the turn is in [agentd.md](agentd.md#one-turn); this page covers the restore point step and what follows from it.

### When a turn gets no restore point

| Case | What happens | Where |
|---|---|---|
| `snapper` is not on `PATH`, or `/etc/snapper/configs/root` is missing: the live ISO (snapper is in the image, no config exists), a development machine, the tests | `Snapshots.available` is false, the turn skips the step, and `undo` says so | `snapshots.py:31-34`, `iso/packages.x86_64:8` |
| `snapshots = false` in the config (read by `agentd` only) | `agentd` builds `_NoSnapshots`, with `available = False`; the os-mcp tools and `bombadil undo` still work (see known gaps) | `agentd.py:2720,2752-2753` |
| A model turn whose provider CLI is not installed | the turn ends with an `error` event before this step, and takes no number | `agentd.py:2074-2079` |
| `snapper` exits non-zero | an `error` event, "no undo point for this turn: snapper failed (...)", and the turn runs without one | `agentd.py:2099-2101` |
| Any other failure inside `create` (no `sudo`, output that is not a number) | the exception reaches `_worker`, which ends the turn with an `error` event; the number was taken, so it is not reused | `agentd.py:2028-2030` |
| A launcher word or another local action (`open`, `stop`, `undo`, `restart`, a picture) | not a turn, so no restore point | `agentd.py:485-502` |
| The AI cannot be asked: `access` is anything but `ready`, for example signed out, offline, or resting (the account's limit used up, or paused by hand) | a prompt that needs the model waits in the queue and no turn starts; a `!` line still runs, with its restore point | `agentd.py:1995-2005`; `tests/test_agentd_rest.py`, `test_a_typed_command_and_launcher_words_still_work_while_it_rests` |

Other moments can make a restore point with any description: the agent's `snapshot` tool, and the development script `scripts/vm-tools/update-in-place`, which takes `before update to main <rev>` (line 64). `undo` ignores descriptions that do not start with `turn:`. The restore points that go through `Snapshots.create` (the turns' and the `snapshot` tool's) are pruned; the one `update-in-place` takes carries no cleanup algorithm and stays for good.

### A turn and a later undo

```mermaid
sequenceDiagram
    actor P as Person
    participant A as agentd
    participant S as snapper
    participant C as provider CLI
    participant R as bombadil-rollback
    P->>A: install htop
    A-->>P: turn_start, then the line "Saving a restore point"
    A->>S: create --cleanup-algorithm number --description "turn:4: install htop"
    S-->>A: restore point 12
    A-->>P: snapshot event, number 12
    A->>C: run the turn with BOMBADIL_TURN_SNAPSHOT=12
    C-->>A: events and system changes
    A-->>P: turn_end, closing line with Undo
    P->>A: undo (the typed word or the Undo button)
    A->>A: hold the queue, stop a running turn
    A->>S: list, pick the newest turn: restore point
    A->>R: sudo bombadil-rollback 12
    R-->>A: old @ kept as @.undone-time, a copy of 12 becomes @
    A-->>P: Undone. System files go back to before "install htop" when you restart.
    Note over P,R: the running system is unchanged until the next boot
    P->>A: restart
    A->>A: systemctl reboot
```

### Undo, step by step

1. The person types `undo`, `undo that`, `undo it` or `undo the last change` (`launcher.py:47`), or clicks `Undo` on the closing line, which sends `{"type": "local", "action": "undo"}` (`shell/PillState.qml:637-640`, `agentd.py:535-538`). A typed line goes through `launcher.match` (`agentd.py:485-502`). It is lowercased, trimmed of `. ! ? , ; :` at both ends, and stripped of spaces, hyphens and underscores, so it must equal one of the four words, in ASCII only (`launcher.normalize`, `_key`, `_lookup`; `undo-that` and `undo_it` match too). A question mark does not stop it: `undo?` runs undo, because only `restart`, `shutdown` and `desk` are sent to the agent when the line ends in `?` (`launcher.py:269`). A leading `!` makes it a shell line. No model is involved. A longer sentence such as "undo the docker install" goes to the agent (`tests/test_launcher.py:79`).
2. `AgentD.local` raises `_hold` so no queued turn starts, takes the `_exclusive` lock and, if a turn is running, stops it and waits until it is gone. The running turn is therefore taken back too, and its restore point is the newest one (`agentd.py:1139-1153`, `test_undo_during_a_turn_takes_back_that_turn_even_with_one_queued`).
3. `Launcher._undo` refuses when `available` is false, reads the marker `undo.json`, lists up to 200 restore points and picks the newest one whose description starts with `turn:` and whose number is below the marker's (`launcher.py:633-663`).
4. `Snapshots.rollback` runs `sudo bombadil-rollback <number>`. Its output is captured because, inside os-mcp, stdout is the JSON-RPC stream (`snapshots.py:57-67`).
5. `bombadil-rollback` mounts the btrfs top level (`subvolid=5`) on a temporary directory, checks that `@snapshots/<n>/snapshot` exists, renames `@` to `@.undone-<YYYYmmdd-HHMMSS>`, makes a `btrfs subvolume snapshot` of the restore point under the name `@` (without `-r`, so the root is writable), and unmounts (`bombadil-rollback:7-16`).
6. `Launcher._undo` writes `undo.json` and returns the sentence for the pill. `agentd` broadcasts `local` events, appends the action to `turns.jsonl` (`kind: "local"`) and tells the next model turn what happened without it (`agentd.py:1156-1172,2120-2124`).
7. The installer strips `subvolid=` from the generated `/etc/fstab` and says in a comment that `/` is mounted by subvolume name, not id, so that a snapshot swapped in under the name `@` is the one found (`bombadil-install:43-46`). Nothing restarts by itself: the person says `restart`, which runs `systemctl reboot` (`launcher.py:844-845`).

**When it applies:** on the next boot, never at once. The docstring (`snapshots.py:58`), the script (`bombadil-rollback:16`), the sentence the pill shows and the VM check below all say so. The mechanism is inferred, not shown by a file in the repository: a mount stays bound to its subvolume when the subvolume is renamed, so the running kernel keeps the renamed `@` until the next boot. Nothing in the repository records how the root subvolume is chosen at boot: the kernel command line comes from `grub-mkconfig` at install time (`bombadil-install:77`) and no generated `grub.cfg` is in the tree. The effect is recorded: the VM run in Tests booted into the swapped-in `@` (`undo-applied`).

**Between the undo and the restart.** The running root still holds what was undone, so a restore point taken before the restart would bring it back. `undo.json` records the boot id (`/proc/sys/kernel/random/boot_id`), and while it matches, the undo is pending (`launcher.py:577-614,892-896`):

- a turn does not clear the marker (`Launcher.clear_undo`, called from `AgentD.turn`, skips a pending one);
- an `undo` says "Already undone" and rolls nothing back when a `turn:` restore point numbered above the marker's is newer than the marker's `covered` (`launcher.py:641-650`). A turn that ran since counts, because the restart takes it back too; so do the restore points of turns an earlier undo already took back, because undo removes none. Every rollback writes a new marker without `covered`. Repeated undos with no turn between therefore alternate: the first two each go one turn further back, then "Already undone", one further back, "Already undone", and so on (see known gaps);
- after the restart the marker is no longer pending. Each further `undo` goes one turn further back, until a turn runs and `clear_undo` removes the marker.

**A turn the limit refused before it changed anything.** `AgentD.turn` reads the marker with `Launcher.undo_marker` before `clear_undo` removes it (`agentd.py:2091-2094`). When the account's limit ends the turn and no step of it changed anything, the turn's restore point is empty, and `Launcher.skip_restore_point` is called with its number and the marker that was read (`agentd.py:2275-2276`, `launcher.py:616-631`):

- with a marker: it is written back, so the next `undo` goes on from where it was. A marker still pending gets `covered` raised to the empty point, so that point does not count as a turn since, and no "Already undone" is said for it;
- with no marker: one is written for the empty point itself (`what` empty, `boot` null, so not pending), and the next `undo` starts below it.

The point stays in snapper. A turn that was not stopped goes back to the front of the queue and runs again when the limit lifts, as a turn of its own with its own number and restore point (`agentd.py:1770-1792`). A turn the limit stopped after it changed something keeps its restore point as the one `undo` takes back. Only the launcher words skip the empty point (see known gaps). The resting state itself is in [the poor man switch](poor-man-switch.md).

**The ways in are not equal.**

| Way in | Path | Writes `undo.json` | A repeat |
|---|---|---|---|
| The four words typed in the pill or sent with `bombadil ask undo` (`bin/bombadil:416-417`: the line goes through `launcher.match` like a typed one), or the Undo button | `AgentD.local`, `Launcher._undo` | yes | before the restart, they alternate between one turn further back and "Already undone", as above; after it, one turn further back each time |
| Any other sentence that means undo | the agent calls `rollback` with no number (its system prompt says to): `Snapshots.undo_last_turn`, which skips the running turn's own restore point (`BOMBADIL_TURN_SNAPSHOT`) | no | each asking is a turn with a restore point of its own, which the next asking picks: see known gaps |
| `bombadil undo` in a terminal (not `bombadil ask undo`, which is the row above) | `bin/bombadil:434-437`, `Snapshots.undo_last_turn` | no | the same restore point each time, until a turn runs and its restore point is the newest |
| `rollback` with a `number`, or `sudo bombadil-rollback N` | any snapshot by number | no | not applicable |

The by-hand path needs no model, no network, and neither `agentd` nor the shell, so it works from a terminal (reaching one from a text console was not checked):

```sh
sudo snapper -c root list        # numbers and descriptions: what Snapshots.list reads
sudo bombadil-rollback 12        # restore point 12 is the root from the next boot
```

`bombadil-rollback` keeps the replaced root as `@.undone-<time>` at the btrfs top level, so, in its own words, an undo can itself be undone by hand. No command in the repository does that.

### What is covered

```mermaid
flowchart LR
    subgraph esp["partition 1, FAT32, at /boot: in no restore point"]
        BOOT["vmlinuz-linux, initramfs, GRUB"]
    end
    subgraph pool["partition 2, btrfs, label bombadil"]
        subgraph root["@ at /: in every restore point"]
            ROOT["/etc, /usr, /var, /opt, /root"]
        end
        subgraph home["@home at /home: in none"]
            HOME["~/Apps, ~/.config/bombadil, ~/.local/state/bombadil, the sign-ins"]
        end
        subgraph snaps["@snapshots at /.snapshots: the restore points"]
            RP["N/snapshot, one per turn"]
        end
        OLD["top level, not mounted in the system namespace: @.undone-time"]
    end
    ROOT -->|"snapper create before each turn"| RP
    RP -->|"bombadil-rollback N: the copy becomes @"| ROOT
    ROOT -.->|"the replaced root is renamed"| OLD
```

This is the layout `bombadil-install` writes (`bombadil-install:16-27,39-46,62-69,93-94`): `@`, `@home` and `@snapshots` at the top level of one btrfs volume, the EFI partition at `/boot`, and `snapper -c root` created on `/`, with the `@snapshots` subvolume mounted at `/.snapshots` in place of snapper's own and a limit of 30 numbered restore points (see Retention and pruning).

| Where | In a restore point | What it means |
|---|---|---|
| `/etc`, `/usr`, `/var` (the pacman database and packages, systemd units, logs), `/opt`, `/root` | yes | an undo takes back installs, config edits and services |
| `/home` (`@home`): `~/Apps`, `~/.config/bombadil`, the sign-ins copied by the installer (`.claude`, `.claude.json`, `.codex`) | no | an undo never deletes the person's files, apps or sign-in, and never brings back a file deleted from home |
| `~/.local/state/bombadil`: `undo.json`, `turns.jsonl`, `turn`, `turns/`, `desk.toml`, `brain.db`, `rest.json`, `mail.db` | no | it is home, so the marker, the logs, the desk, the brain's index, a pause or limit of the AI (`src/bombadil/rest.py:71`) and the mail service's notes (drafts, marks, receipts) survive the swap |
| `/boot`: kernel, initramfs, GRUB (the EFI partition, FAT) | no | the installer puts there the kernel of the root it copies, the newest `/usr/lib/modules/*/vmlinuz` by version, and not one from the boot medium (`bombadil-install:14-15,41`). The agent installs with `pacman -Syu`, which also updates every other package (`providers.py:43-48`), so an install turn can replace the kernel while `/boot` stays outside every restore point. A rollback after a kernel update can leave the newer kernel with the older modules (see known gaps) |
| `/.snapshots` | not part of itself | restore points survive an undo, including those newer than the one chosen |

### Retention and pruning

On `main`, restore points are pruned to the newest 30, and no hourly points are made.

- **Each point is marked for pruning.** `Snapshots.create` passes `--cleanup-algorithm number` (`snapshots.py:45-46`), so a restore point is one of snapper's numbered snapshots. Snapper's other algorithms (`timeline`, `empty-pre-post`) are named per snapshot too ([snapper manual](http://snapper.io/manpages/snapper.html)); no restore point is made under them.
- **The installer sets the limit and turns the timer on.** In its chroot block, after `snapper --no-dbus -c root create-config /`, it runs `snapper --no-dbus -c root set-config TIMELINE_CREATE=no NUMBER_CLEANUP=yes NUMBER_LIMIT=30` and `systemctl enable snapper-cleanup.timer` (`bombadil-install:62-69`). `TIMELINE_CREATE=no` turns the hourly points off: the turn's restore points are the timeline.
- **The timer enforces the limit, not the creation.** `create` deletes nothing; snapper's cleanup runs when `snapper-cleanup.timer` fires, so more than 30 can exist between two runs. The smoke checks that a restore point shows `"cleanup": "number"` in `snapper list` (`snapshot-is-prunable`), and that the config holds `NUMBER_LIMIT="30"` and `TIMELINE_CREATE="no"` with the timer enabled (`snapshots-are-pruned`, `bombadil-smoke:493-495`). It does not wait for a cleanup to delete anything. The snapper manual also describes a minimum age (`NUMBER_MIN_AGE`) that spares young snapshots from the number cleanup; the installer leaves it at the template's value, and this was not checked on a machine.

Not pruned:

- `@.undone-<time>`, one per undo (`bombadil-rollback:13-14`), is removed by nothing.
- A restore point made with no cleanup algorithm stays for good: the one `scripts/vm-tools/update-in-place:64` takes, or one made by hand with `snapper create`.
- A machine installed before the installer set the limit keeps the config it was installed with, because the installer runs once. That installer enabled only `greetd` and `NetworkManager`, so nothing in the repository runs the cleanup there, and nothing applies those settings to it; reaching it needs a step that runs `set-config`, which the installed OS brief designs as numbered steps inside an update.

What undo can reach is bounded by what snapper keeps, about the newest 30 restore points on an installed machine, and then by the readers. The launcher looks at up to 200 (`launcher.py:640`), so on an installed machine it is never the limit. `undo_last_turn` (the agent's `rollback` with no number and `bombadil undo`) and `list_snapshots` read the newest 20 (`snapshots.py:49,76`), so they see 20 of the 30.

Designed, not on `main`: the fuller design is a pre and post pair for every turn, pairs with no change dropped at the end of the turn, the last 100 changed pairs kept instead of 30 restore points, and `@.undone-*` pruned (the installed OS brief says by a daily timer, older than 14 days). See [the UX brief's decisions](../design/ux-brief.md#decisions-i-picked-a-default-for) and [the installed OS brief](../design/installed-os-brief.md#after-these-in-order). What is on `main` of it is in [the roadmap](../roadmap.md).

### How undo is explained to the person

Every sentence below is written by `agentd`, the launcher or the shell, with no model. The design says undo must say exactly what it covered ([UX brief, rule 4](../design/ux-brief.md#the-rules)). On `main` that means the turn it took back, named by its prompt, and the two areas: system files change at the restart, home and apps do not. It does not list files.

| Moment | What the person sees | Where |
|---|---|---|
| A restore point is being saved | the live line "Saving a restore point" | `agentd.py:2097` |
| Snapper failed | "no undo point for this turn: snapper failed (...)" | `agentd.py:2101` |
| A turn changed something and was not stopped | the closing line stays until the next prompt, with `Undo` and `Details`. A stopped turn gets no sticky line and no `Undo` button; its restore point still exists and the word `undo` still works. While a notice waits (new mail, a draft that is ready, a warning), the sticky line gives way after 12 seconds, or at once for a warning, and takes the button with it; the word still works. A step that no restore point can undo (formatting a disk, deleting under home) shows "can’t be undone" | `shell/StatusLine.qml:68-70,209-233`, `shell/PillState.qml:298` (`sticky = changed && !stopped`), `agentd.py:2278-2280`, `narrate.py:450-486` |
| A turn the account's limit stopped, and that goes back in the queue | the closing line says what it changed and when it carries on, or, when it changed nothing, the resting line with no `Undo`; one that changed something keeps `Undo` and `Details`. `undo` skips an empty restore point | `shell/PillState.qml:282,298,406`, `agentd.py:2267-2276,2423-2430` |
| A step that no restore point can undo is running | the line above the pill gets a red edge, with the exact command under it; the desk's Now card gets a red edge and the caption "can't be undone · Esc stops it" | `shell/StatusLine.qml:20-26`, `shell/DeskState.qml:546-549` |
| A turn that changed something is done and was not stopped | the desk's Now card says "done in N s · Undo is above the pill" | `shell/DeskState.qml:556` |
| An undo went through | "Undone. System files go back to before “<prompt>” when you restart. Your home folder and apps stay as they are." The line stays 15 seconds instead of 5, and longer while a picture the person asked for is up | `launcher.py:664-665`, `shell/PillState.qml:317`, `shell/StatusLine.qml:68-70` |
| An undo asked again before the restart, after turns | "Already undone: when you restart, system files go back to before “<prompt>”, and that takes back everything since too." | `launcher.py:649-650` |
| An undo that cannot go back | "Nothing to undo yet." (no earlier `turn:` restore point); on the live ISO "Undo starts once Bombadil is installed. The live system keeps no restore points."; with `snapshots = false` or no snapper config on an installed system "Undo is off here: this system keeps no restore points." | `launcher.py:660,634-637` |
| In a terminal | `bombadil history` ends each turn with "(restore point N)", a turn the limit cut off and its rerun as two rows; `bombadil watch`, which the closing line's `Details` opens, shows a dim "restore point N" line for the `snapshot` event and the dim status line "Saving a restore point" | `src/bombadil/watch.py:283,64-65,76-77` |
| The next model turn | its prompt starts with "[Done by the user without you since your last turn: 'undo': Undone. ...]" | `agentd.py:1169,2120-2124` |

The agent is told in its system prompt to use `rollback` when the person says undo (`providers.py:43-44`). The narrator words that tool call as "Undoing the last change" and marks it as a system step (`narrate.py:907-908`); a `bombadil undo` in a shell line gets the same words (`narrate.py:645-646`).

### What the installed-OS redesign changes (designed)

The [installed OS brief](../design/installed-os-brief.md) treats the installed machine as the product and decides, in [piece 1](../design/installed-os-brief.md#1-the-disk-that-lasts-layout-v1-and-a-refresh-that-keeps-your-home) and the pieces after it, how restore points change; the [UX brief](../design/ux-brief.md#decisions-i-picked-a-default-for) decides what undo covers and when it applies. Two parts of it are on `main` and are described above: the installer takes the kernel from the copied system (the first commit of piece 1, `bombadil-install:14-15,41`), and restore points use number cleanup with a limit of 30, no hourly points and the cleanup timer on (part of piece 4, see Retention and pruning). The rows below are the rest, and none of them is in the code. The brief's status is Designed; [the installed OS page](installed-os.md) lists all thirteen pieces.

| Designed | Differs from `main` by |
|---|---|
| Layout v1: top-level `@`, `@home`, `@snapshots` plus `@log` and `@pkg` beside `@`, and nested subvolumes inside `@home` for what a home restore point skips | `main` has the first three only (`bombadil-install:23`) |
| Encryption by default: partition 2 is LUKS2 with btrfs inside, three keyslots (the password GRUB opens, a key file in the initramfs so the password is typed once, and a recovery key shown once) | `main` formats partition 2 as plain btrfs and gives `user` an empty password (`bombadil-install:21,60`) |
| A snapper config for `/home` (`snapper -c home create-config /home`, `TIMELINE_CREATE=no`) for undo of home files | `main` has `root` only |
| The ESP at `/efi`, the kernel and initramfs in `@/boot` so every restore point carries its own kernel, GRUB on the ESP | `main` mounts the ESP at `/boot` (`bombadil-install:40`) |
| `bombadil-rollback` refuses a snapshot with no `boot/vmlinuz-linux`, and gains `--device DEV` for use from the install stick | `main` does neither (`bombadil-rollback:7,11-12`) |
| Snapshot 1, "Bombadil <version>, as installed", and one for programs brought from the stick | `main` creates no snapshot at install |
| Undo that applies at once for files under `/etc` and home, with "redo" | `main` applies every undo at the next boot |
| A start that fails twice puts itself back and the boot menu lists restore points, a Repair mode on the stick, a rescue page on tty2 | no code; `main` has `bombadil-rollback` run by hand as the only recovery, and no bootloader entry for a restore point (`iso/packages.x86_64` has no `grub-btrfs`) |

## Interfaces other pieces depend on

The full tool list is in [os-mcp.md](os-mcp.md#the-tool-catalogue).

| Name | Kind | What it is |
|---|---|---|
| `snapshot` (`description`, required) | os-mcp tool | creates a restore point with that exact description; returns `snapshot <n>` or `snapshots unavailable on this system` (`mcp_server.py:111-114`) |
| `list_snapshots` | os-mcp tool | the newest 20 as `{number, description}`; snapper's number 0 is left out (`mcp_server.py:116-118`) |
| `rollback` (`number`, optional) | os-mcp tool | with a number: `bombadil-rollback <number>`, returns `rolled back, reboot to apply` or `snapshots unavailable`. Without: `undo_last_turn`, returns `rolled back to snapshot <n> (<description>), reboot to apply` or `nothing to undo` (`mcp_server.py:120-127`) |
| `undo`, `undo that`, `undo it`, `undo the last change` | launcher words | `Launcher._undo`, matched with the folding described in Undo step 1 (the table of all launcher words is in [agentd.md](agentd.md#launcher-words)) |
| `{"type": "local", "action": "undo"}` | socket message, client to `agentd` | what the Undo button sends. For a typed launcher word, `agentd` sends the same message back to the typing client as an acknowledgement, ahead of the `local` start and done events (`agentd.py:500`, `tests/test_agentd.py:320`) |
| `local` event with `action: "undo"`, phase `start` then `done` (`ok`, `text`) | socket event | "Undoing the last change", then the sentence. It also carries `target` and `verb` (`agentd.py:1160-1164`) |
| `snapshot` event, `number` | socket event | the restore point was saved |
| `turn_start` event, `snapshot` | socket event field | always `null`: the event goes out before snapper runs, and the number arrives later in the `snapshot` event (`agentd.py:2072,2103`) |
| `turn_end` event, `changed` | socket event field | `bool(narrator.done)`, true when a step of the turn changed something (`agentd.py:2279`; `false` on the early ends at 2077 and 2107). With `stopped` false it keeps the closing line and its `Undo` button |
| `turn_end` event, `irreversible` | socket event field | `narrator.irreversible`: a step that no restore point can undo. The closing line then shows "can’t be undone" (`agentd.py:2279`, `shell/PillState.qml:280`, `shell/StatusLine.qml:215-221`) |
| `status.snapshots` | socket message field | `Snapshots.available`; `bombadil status` prints it, and no shell file reads it (`agentd.py:601`) |
| `snapshot` and `n` in a `turns.jsonl` row | file fields | the turn's restore point number, or null, and the turn's number, the `<n>` of its description (`agentd.py:2407-2421`; a turn the limit cut off has `requeued: true` and its own `snapshot`, and its rerun is another row). The brain keeps `snapshot` in the turn's metadata and drops it when it is not an integer (`brain/ingest.py:601`, `brain/witnesses.py:268-269`) |
| `BOMBADIL_TURN_SNAPSHOT` | environment variable | set by `agentd` on the CLI of a turn that has a restore point; forwarded to os-mcp through `providers.MCP_ENV` so the Codex server sees it (`providers.py:514-517,554`) |
| `bombadil undo` | command | `Snapshots.undo_last_turn`, exit 1 with `nothing to undo` (`bin/bombadil:434-437`) |
| `sudo bombadil-rollback N` | command, `iso/airootfs/usr/local/bin/bombadil-rollback` | the swap, for any snapshot number |
| `snapshots` | config key, boolean, default `true` | `/etc/bombadil/config.toml` ships `snapshots = true`; `~/.config/bombadil/config.toml` overrides it; read once when `agentd` starts (`config.py:25,52`, `agentd.py:2714,2720`). Only `agentd` reads it: it decides the turn restore points, the launcher's undo and `status.snapshots`. The os-mcp tools and `bombadil undo` build `Snapshots()` without it (`mcp_server.py:42`, `bin/bombadil:435`) |
| `Snapshots(config_name="root", runner=subprocess.run, configs_dir=/etc/snapper/configs)` | Python class | the one Python class that runs snapper. `agentd`, os-mcp and `bombadil undo` use it. The installer and `update-in-place` run the `snapper` command themselves and not this class; the smoke checks `snapshots-available` and `snapshot-turn` go through it (`bombadil-smoke:490-491`). `runner` and `configs_dir` are seams a test can use, but `available` also needs `snapper` on `PATH` (`shutil.which`, `snapshots.py:34`), so a test that goes through `runner` patches that too, as `tests/test_snapshots.py` does (it passes `runner` and `configs_dir`). Two more tests pass `configs_dir` (`tests/test_appkit_tools.py:28`, `tests/test_launcher.py:192`); the rest subclass it or use `agentd._NoSnapshots` |
| `Snapshots.available`, `.create`, `.list(limit=20)`, `.rollback`, `.undo_last_turn(before=None)` | methods | what everything above calls |
| `Launcher.undo_marker()`, `Launcher.skip_restore_point(snapshot, before)` | methods | `AgentD.turn` reads the marker before `clear_undo` and, when the account's limit ended the turn and no step of it changed anything, hands the turn's restore point number and that marker to the second (`agentd.py:2093-2094,2275-2276`, `launcher.py:616-631`) |

## Where state lives

| What | Where | Notes |
|---|---|---|
| The restore points | snapper config `root` at `/etc/snapper/configs/root`; the snapshots at `/.snapshots/<n>/snapshot` | `@snapshots` is its own subvolume, mounted with mode 750 (`bombadil-install:93-94`) |
| The pruning settings | `NUMBER_CLEANUP="yes"`, `NUMBER_LIMIT="30"` and `TIMELINE_CREATE="no"` in `/etc/snapper/configs/root`, and the unit `snapper-cleanup.timer` enabled in the installed system | set once by the installer (`bombadil-install:67-69`). The config is under `/etc`, so it is in every restore point; a machine installed with an earlier installer was not given them (see Retention and pruning) |
| The root an undo replaced | `@.undone-<YYYYmmdd-HHMMSS>` at the btrfs top level, not mounted in the system's mount namespace | nothing in the repository removes it. The brain watcher mounts the top level read-only in a namespace of its own (`src/bombadil/brain/watch.py:925-945`, [brain](brain.md)), where it can see this folder |
| The undo marker | `~/.local/state/bombadil/undo.json`: `snapshot`, `what`, `boot`, `t`, and `covered` once set | home, so it survives the swap; the directory is `paths.state_dir()`. A marker written by `skip_restore_point` for an empty point has `what` empty and `boot` null |
| The turn log | `~/.local/state/bombadil/turns.jsonl` | each turn row carries `snapshot` and `n`; `bombadil history` prints it as "(restore point N)" (see [agentd.md](agentd.md#the-turn-log)) |
| The last turn number begun | `~/.local/state/bombadil/turn` | one number, written before snapper runs; with the log's last `n` it is where the next `turn:<n>` starts (`agentd.py:2642-2663`). The number is also the brain's key for the turn, `turn:<n>` (`brain/ingest.py:561`, read back in `brain/focus.py:154`), which is why `_begun` (`agentd.py:2647-2648`) keeps the number of a turn that crashed before its row, since its restore point and the brain already carry it: two turns with one number would share one thing in the brain |
| The per-turn event log | `~/.local/state/bombadil/turns/<ms>-<turn>.jsonl` | holds the `snapshot` event; `bombadil watch` replays it, and the closing line's `Details` opens it. `<turn>` is the queue id (`AgentD.next_id`, which starts again at 1 when `agentd` starts), not the `n` of the description (`agentd.py:287,2064`, `watch.py:301-304`) |
| The setting | `/etc/bombadil/config.toml`, `~/.config/bombadil/config.toml` | |
| The conversation and the session's model | `$XDG_RUNTIME_DIR/bombadil/agentd-state.json` (`paths.runtime_dir()`): `provider`, `session_id`, `model` | in no restore point. `agentd` writes it after a turn that worked, when `session_id` is dropped and when the model is switched, and reads it when it starts (`agentd.py:374-402`), so a restart of the daemon keeps the conversation. The runtime directory does not survive a reboot (its own docstring says so, `agentd.py:380-381`), so the reboot that applies an undo loses it |
| In memory | `AgentD._hold`, `AgentD.notes`, `AgentD.turn_logs` | gone when `agentd` restarts; `AgentD.turns` is rebuilt from the two files above, and `AgentD.session_id` is reloaded from `agentd-state.json` |

## Principles it keeps

- [Full access, with undo instead of guard rails](../principles.md#full-access-with-undo): every turn that reaches the restore point step on a system with snapper and its `root` config gets one, so nothing in front of the agent asks permission. A snapper failure lets the turn run without one and shows an `error` event. The trap: adding a confirmation before something undo could reverse, or adding a way to change the root outside `AgentD.turn`, which takes no restore point. The other trap is claiming more than undo does: it covers the root only, and only as far back as snapper keeps, which is about the newest 30 restore points.
- [Recovery never goes through the part that broke](../principles.md#recovery-without-the-broken-part): `undo` is a launcher word that `agentd` answers without the model, and `bombadil undo` and `sudo bombadil-rollback` need neither `agentd` nor the shell. The trap: sending undo through the agent. An undo run by the agent is itself a turn with a restore point of its own, and rolling back to that one does nothing; `BOMBADIL_TURN_SNAPSHOT` and the `before` argument of `undo_last_turn` make it skip that one.
- [Records answer before models, and the machine explains itself](../principles.md#records-before-models): the undo sentence is built from the restore point's description and the marker. The trap: letting a model word it, or putting something in a restore point description that is not the person's own prompt.
- [Every piece degrades, and nothing waits on a model it does not need](../principles.md#degrade-and-recover): without snapper or its config every method is a no-op that reports `available = False`, and a snapper failure is an `error` event, not a lost turn. The trap: raising anything other than `subprocess.CalledProcessError` from `create` (the turn ends), or calling snapper on the event loop (it can take a second).
- [Plain files stay the truth](../principles.md#plain-files-stay-the-truth): the marker, the turn number and the turn log are small files in `~/.local/state/bombadil`, outside every restore point so they survive the swap. The trap: putting state that must outlive an undo under `/etc` or `/var`, where undo takes it back; `src/bombadil/desk.py` documents the same rule for `desk.toml`. A second trap is changing how turns are numbered: the number in `turn:<n>` is shared with the brain's turn keys, so it must stay unique across crashes and restarts.

## Extending it

### Change the retention limit

Pruning is already on `main` (see Retention and pruning); these steps change it.

1. The limit is the installer's. Edit `NUMBER_LIMIT=30` in the `set-config` line (`bombadil-install:68`), and the matching lines in `tests/test_iso_profile.py` (`test_the_installed_system_prunes_its_restore_points_and_takes_no_hourly_ones`) and in the smoke check `snapshots-are-pruned` (`bombadil-smoke:495`, which looks for `NUMBER_LIMIT="30"`). Keep the `set-config` after `create-config` and keep `systemctl enable snapper-cleanup.timer` (`bombadil-install:69`): without the timer nothing runs the cleanup. The installed OS brief cuts the hourly timeline on purpose, so leave `TIMELINE_CREATE=no`.
2. A machine that is already installed keeps its config, because the installer runs once. By hand it takes `sudo snapper -c root set-config NUMBER_LIMIT=<n>`; for every machine it needs a step that runs `set-config`, which the installed OS brief designs as numbered steps inside an update.
3. Keep the readers in line with the limit: the launcher reads up to 200 restore points (`launcher.py:640`), the agent's `rollback` and `list_snapshots` and `bombadil undo` the newest 20 (`snapshots.py:49,76`). A limit above 20 is out of their reach.
4. Still to build: pruning `@.undone-*`, and a cleanup algorithm for the restore point `scripts/vm-tools/update-in-place:64` takes. `@.undone-*` are not snapper's: deleting one needs the top level mounted the way `bombadil-rollback:8-9` does it, and no existing unit or script does this. To put the update script's point under the limit, add `--cleanup-algorithm number` to that `snapper create`.
5. Test the argument list of `create` as `tests/test_snapshots.py::test_a_turns_restore_point_is_one_snapper_may_prune` does: `configs_dir=tmp_path` holding a file named `root`; `snapper` found, by patching `snapshots.shutil.which` (`monkeypatch.setattr(snapshots.shutil, "which", lambda name: "/usr/bin/snapper")`); and `runner=` returning an object whose `.stdout` is `"7\n"`. Without the second, `available` is false and `create` returns `None` before it reaches the runner, so the test records nothing. The installer lines are asserted as text by `tests/test_iso_profile.py`, which runs no snapper; only the smoke on a real disk checks the config and the timer.

### Add another moment that takes a restore point

1. Choose the description prefix. `undo` picks only descriptions that start with `turn:`. Another prefix shows in `list_snapshots`, can be rolled back by number, and is skipped by `undo`. To let undo reach it, change the three `startswith("turn:")` tests: `launcher.py:644` (the turns that count as covered by a pending undo, for "Already undone"), `launcher.py:656` (the restore point the launcher picks) and `snapshots.py:79` (the agent's `rollback` and `bombadil undo`), and change `_snapshot_prompt` (`launcher.py:351-355`), which cuts the prefix off for the sentence.
2. Inside `agentd`: call `self.snaps.create(description)` through `asyncio.to_thread`, only when `self.snaps.available`, and catch `subprocess.CalledProcessError` as `agentd.py:2099-2101` does. Say it on screen: the turn sends a `status` event, then a `snapshot` event.
3. From the agent: nothing to add. The `snapshot` tool takes any description, and the narrator words it "Saving a restore point" (`narrate.py:903-904`).
4. From a script on the machine: `sudo snapper -c root create --description "<text>"`, as `scripts/vm-tools/update-in-place:64` does. That point carries no cleanup algorithm, so it is never pruned; add `--cleanup-algorithm number` to put it under the limit (see Retention and pruning).
5. If the moment can run while an undo waits for its restart, remember the rule in `launcher.py:577-579`: a restore point taken then holds the undone changes.
6. Test with a recording fake, as `RecordingSnaps` in `tests/test_agentd.py:603-621`: subclass `agentd._NoSnapshots`, set `available = True`, record `create`.

### Add a recovery path that works without the model

1. Pick how it is reached.
   - An exact word: add it to `CORE_COMMANDS` (`launcher.py:45-54`; these are checked before app names, so they always mean the same thing). Add a method `Launcher._<kind>` that returns `(ok, one plain sentence)`; `Launcher.run` finds it by name (`launcher.py:419-426`). Add the `doing` and `failed` strings (`launcher.py:390,410`). If it must end the running turn first, add the kind to the tuple in `AgentD.local` (`agentd.py:1139`). The button form works without more code, because `_action` accepts the kinds of `CORE_COMMANDS`, `BRAIN_COMMANDS` and `UTILITY_COMMANDS` (`agentd.py:2590-2595`). If the word changes the system, also add its kind to the tuple `("restart", "shutdown", "desk")` in `launcher.match` (`launcher.py:269`), or the line "<word>?" runs it too (`undo?` does run undo), and add a `"<word>?"` row to `test_everything_else_goes_to_the_agent`; see [agentd](agentd.md#add-a-launcher-word). Add the kind to `NO_COMPLETE` if Tab should never land on it (`launcher.py:138`).
   - A terminal command that needs no `agentd`: add a branch to `main()` in `bin/bombadil`, like `undo` (`bin/bombadil:434-437`).
   - A root script: put it in `iso/airootfs/usr/local/bin/`, list its mode in `file_permissions` in `iso/profiledef.sh` (mkarchiso copies without modes; see the entry for `bombadil-rollback`, line 21), and add it to `SYSTEM` in `scripts/vm-tools/update-in-place` so in-place updates carry it. `user` runs it with `sudo`, which asks no password (`iso/airootfs/etc/sudoers.d/bombadil`).
2. Depend on nothing that can be the broken part: no model, no network, no Quickshell, and for a command or a script no `agentd`. Read what it needs from files and btrfs.
3. Say in one plain sentence what it did and what it did not cover, as `_undo` does.
4. Record it. `AgentD.local` logs launcher actions to `turns.jsonl` and tells the next turn (`agentd.py:1169-1172`); a terminal command or a script does neither, so a recovery that matters to the next turn must write its own marker, as `undo.json` does.
5. Test a word in `tests/test_launcher.py` (the parameter table at the top, and a fake like `Snaps`) and in `tests/test_agentd.py` (`test_launcher_words_never_start_a_turn`). A script can only be run by the VM smoke: add a step to the `undo` mode of `bombadil-smoke` (`bombadil-smoke:456-504`).
6. Not built: the rescue page on tty2 and the start that puts itself back. They are designed in the briefs linked above.

## Tests

| What | Where |
|---|---|
| The four words match, and sentences with undo in them go to the agent | `tests/test_launcher.py`: `test_exact_words_open_locally` (the `undo` and `Undo that` rows), `test_everything_else_goes_to_the_agent` |
| One turn further back on the first two undos, the marker, "Already undone" after a turn, no restore points | `tests/test_launcher.py`: `test_undo_goes_back_one_turn_at_a_time_until_a_new_turn`, `test_a_turn_before_the_restart_does_not_bring_back_what_was_undone`, `test_undo_without_restore_points_says_so` |
| The tools, and `undo_last_turn` skipping the running turn's restore point | `tests/test_mcp_server.py`: `test_snapshot_and_undo`, `test_undo_inside_a_turn_skips_that_turns_snapshot` |
| The restore point comes after `turn_start`, can be stopped, and an undo during a turn takes that turn back before a queued one starts | `tests/test_agentd.py`: `test_turn_start_comes_before_the_restore_point`, `test_stop_while_the_restore_point_is_saved_ends_the_turn_before_its_cli`, `test_undo_during_a_turn_takes_back_that_turn_even_with_one_queued`, `test_launcher_words_never_start_a_turn` |
| The turn number, and so the `turn:<n>` of a restore point, goes on across restarts and past a turn cut off by a crash | `tests/test_agentd.py`: `test_turn_numbers_go_on_across_restarts`, `test_rows_of_other_kinds_are_not_turns`, `test_a_restarted_agentd_numbers_its_next_turn_and_restore_point_after_the_last`, `test_a_turn_cut_off_by_a_crash_keeps_its_number`, `test_a_row_cut_short_by_a_crash_does_not_swallow_the_next` |
| A turn the limit refused before it changed anything is skipped by `undo`: the marker is put back, or written for the empty point, and a pending marker covers it | `tests/test_launcher_rest.py`: `test_a_turn_refused_at_once_is_skipped_by_undo_and_the_one_before_it_is_taken_back`, `test_the_undo_the_user_had_made_before_the_refused_turn_carries_on_from_where_it_was`, `test_an_undo_the_restart_has_applied_is_put_back_too_so_the_next_undo_goes_one_turn_further`, `test_a_pending_undo_stays_and_covers_the_empty_point_so_it_adds_no_extra_press`; through `AgentD`, `tests/test_agentd_rest.py`: `test_the_refused_turns_empty_restore_point_is_not_what_undo_takes_back` |
| `snapshots` defaults to true | `tests/test_config.py`: `test_defaults` |
| Every restore point from `Snapshots.create` is made under snapper's number cleanup | `tests/test_snapshots.py`: `test_a_turns_restore_point_is_one_snapper_may_prune` (the command line, through a fake `runner`: `--cleanup-algorithm number`, `--print-number`, the description last) |
| The installer limits the restore points to 30, makes no hourly ones, enables the cleanup timer and takes the kernel from the root it copies; the agent installs with `pacman -Syu` and is told a replaced kernel needs a restart | `tests/test_iso_profile.py`: `test_the_installed_system_prunes_its_restore_points_and_takes_no_hourly_ones` (the installer's `set-config` and timer lines as text, and that they come after `create-config`), `test_the_installer_takes_the_kernel_from_the_system_it_copies_not_from_the_boot_medium`, `test_pacman_has_an_active_mirror_and_the_agent_upgrades_as_it_installs`, `test_the_agent_is_told_a_replaced_kernel_needs_a_restart`. On a real disk, the smoke checks `snapshot-is-prunable` and `snapshots-are-pruned` (`bombadil-smoke:493-495`) |
| The closing line: `Undo` and `Details` after a turn that changed something, `Undo` sends the `local` message without also opening the details, no `Undo` after a stop, `Undo` while disconnected keeps the line and says why, the red edge on the line while a step no restore point can undo runs | `tests/test_pill_qml.py`: `test_a_turn_streams_its_steps_and_closes_with_undo`, `test_undo_on_the_line_does_not_also_open_details`, `test_an_irreversible_step_is_red_and_says_so_after` (the edge only), `test_stop_says_what_it_stopped_and_offers_no_undo`, `test_undo_while_disconnected_keeps_the_line_and_says_why`; `tests/test_notice_qml.py`: `test_a_warning_is_read_over_a_closing_line_that_would_stay_for_good` (no `Undo` button while a warning has the line), `test_news_waits_behind_a_sticky_line_and_comes_when_it_gives_way`, `test_a_sticky_line_with_no_notice_waiting_stays_with_its_undo` |
| The desk's Now card: "done in N s · Undo is above the pill" only when the turn changed something, and the red edge with the caption "can't be undone · Esc stops it" | `tests/test_desk_qml.py`: `test_now_follows_a_whole_turn`, `test_the_done_line_only_promises_undo_when_there_is_one`, `test_an_irreversible_step_is_red_with_its_words_even_when_no_step_is_marked_current`; `tests/test_desk_cards_qml.py`: `test_a_step_no_restore_point_can_undo_is_red_with_its_caption_under_the_command` |
| The wording in a terminal: "restore point N" in `bombadil watch` and in `bombadil history`, and the `rollback` tool step worded as "Undoing the last change" | `tests/test_watch.py`: `test_details_show_each_step_its_command_and_output` (line 24), `test_history_lists_turns_and_launcher_actions` (line 69), `test_history_ends_a_requeued_turn_with_its_cut_off_line` (a cut-off turn and its rerun, restore points 5 and 6); `tests/test_narrate.py`: `test_tools_in_plain_words` (the `rollback` row, line 81) |
| The word typed into the real pill, through the real bar and `agentd`: a `local` event with `action` `undo` and phase `done` comes back. The container has no snapper, so only the "Undo is off here" branch runs, and the check asserts that the event arrives, not its text | `tests/desktop/driver.py`, step 6 (lines 422-430), run by `tests/desktop/run.sh` ([development](../contributing/development.md#the-desktop-test)) |
| The whole path on a real btrfs disk: install, a restore point, a change under `/etc`, `bombadil undo`, a reboot, the change gone | the `undo` mode of `iso/airootfs/usr/local/bin/bombadil-smoke`, run by `MODE=install scripts/test-vm.sh`; `MODE=installed scripts/test-vm.sh` boots the disk the last install run left, for the second boot ([VM testing](../contributing/vm-testing.md#running-it)). A recorded run (build `1ed8f46`, 2026-09-27) passed `snapshots-available`, `snapshot-turn`, `undo` and, after the reboot, `undo-applied`. Since that build `bombadil-rollback` and the installer's subvolume and `fstab` lines are unchanged, while `snapshots.py` (the cleanup algorithm), the installer's snapper lines (the `set-config` and the timer) and its kernel copy changed (the GRUB menu and sign-in lines did too). The mode has since gained more checks than the recorded run had, among them the pruning checks `snapshot-is-prunable` and `snapshots-are-pruned` (`bombadil-smoke:493-495`), brain checks ahead of these steps (`bombadil-smoke:459-488`), the mail checks (`bombadil-smoke:276-280`) and the checks that `agentd`, the bar and `mako` come back after a kill (`bombadil-smoke:207-213`), none of which the recorded run covers |

```sh
python3 -m pip install -e '.[dev]'    # pytest and pytest-asyncio: the agentd tests need the second
python3 -m pytest -q tests/test_launcher.py tests/test_launcher_rest.py tests/test_mcp_server.py tests/test_config.py -k "undo or snapshot or config"
python3 -m pytest -q tests/test_agentd.py tests/test_agentd_rest.py -k "restore_point or undo or launcher_words or turn_numbers or rows_of_other or restarted_agentd_numbers or crash"
python3 -m pytest -q tests/test_snapshots.py tests/test_watch.py tests/test_narrate.py tests/test_iso_profile.py
python3 -m pip install -e '.[dev,apps]'    # adds PySide6, which the QML rows need
python3 -m pytest -q tests/test_pill_qml.py tests/test_notice_qml.py tests/test_desk_qml.py tests/test_desk_cards_qml.py
```

Setting up a checkout is in [development](../contributing/development.md#set-up); the VM tools are in [development](../contributing/development.md) and the installer in [iso-and-install](iso-and-install.md). The QML test files skip when PySide6 is missing (`pytest.importorskip` at the top of each), and PySide6 is in the `apps` extra, not in `dev`: without the second install their rows skip silently.

The tests replace snapper with fakes that override `available`, `list` and `rollback` (or `create`); `tests/test_snapshots.py` runs the command line of `Snapshots.create` through a fake `runner`. Only the VM run exercises the command lines of `.list` and `.rollback`, the parsing of snapper's JSON, `bombadil-rollback`, and the installer's subvolume layout and `fstab` rewrite. Nothing runs snapper's cleanup, so no check shows a restore point past the limit being deleted. That run uses `bombadil undo`, so the launcher's marker is covered by unit tests only; `tests/vm/btrfs-kernel.sh` boots a VM on the installer's layout for the brain's watcher, and no sample there runs snapper or `bombadil-rollback`. No test undoes three times before a restart: `test_undo_goes_back_one_turn_at_a_time_until_a_new_turn` stops after two, so the alternation in known gaps is unseen. No test covers the export of `BOMBADIL_TURN_SNAPSHOT` into the CLI's environment (`agentd.py:2134-2137`) or its place in `providers.MCP_ENV` (`providers.py:516`): `tests/test_mcp_server.py` clears the variable at line 101 and sets it by hand at line 114, and asserts only `BOMBADIL_TURN` and `BOMBADIL_SOCKET` in `MCP_ENV` (line 399), so dropping the export (both providers) or the `MCP_ENV` entry (the Codex server, which starts with only the variables listed there) passes every test, and the agent-run undo then picks the running turn's own restore point and changes nothing. No test covers the snapper failure branch (`CalledProcessError` giving "no undo point for this turn", `agentd.py:2099-2101`; see [known issues](../known-issues.md)), the 15-second undo line (`PillState.qml:317`; the `15000` at `tests/test_pill_qml.py:747` is for receipts) or the closing row's "can’t be undone" label (`test_an_irreversible_step_is_red_and_says_so_after` only takes a screenshot when `BOMBADIL_SCREENS` is set). The "restore point N" wording has three `tests/test_watch.py` tests named above and four assertions: line 37 (`bombadil watch`), line 78 and lines 228-229 (`bombadil history`, restore points 5 and 6). The dim "Saving a restore point" line of `bombadil watch` (`watch.py:76-77`) has no test.

## Known gaps

Each gap is checked in code. Gaps that other pages also list are linked, not repeated: see [known issues](../known-issues.md).

| Gap | Evidence |
|---|---|
| Home is in no restore point. A file deleted under home or a change to `~/Apps` is not taken back, and the undo sentence says so only after the fact. Before the step runs, `_irreversible` marks `rm`, `find -delete` and `find -exec rm` under home as "can’t be undone"; other shell commands that change home, such as `mv` or `cp` over a file, are not marked. The mail service's drafts, marks and receipts are in home too (`paths.mail_db()`), and the agent's mail tools are narrated as lines that claim no change, so a turn that only drafts mail gets no `Undo` | `bombadil-install:23,27`, `narrate.py:450-486,943-954`, `paths.py:79-80` |
| The kernel and initramfs are on the EFI partition at `/boot`, outside `@`. An undo after a kernel update can pair a newer kernel with older modules. The installer copies the kernel there from the root it copies, and the agent installs with `pacman -Syu`, which also updates every other package, so an install turn can replace the kernel, which makes this case likelier | `bombadil-install:14-15,39-41,48-52,76`, `providers.py:43-48`; [known issues](../known-issues.md) |
| Nothing removes `@.undone-<time>`, one per undo, or a restore point made with no cleanup algorithm (the one `scripts/vm-tools/update-in-place:64` takes), so free space still shrinks with use. A machine installed before the installer set the limit keeps the config it was installed with, and that installer did not enable the cleanup timer | `bombadil-rollback:13-14`, `scripts/vm-tools/update-in-place:64`, `bombadil-install:67-69`; Retention and pruning above; [known issues](../known-issues.md) |
| Only the launcher words keep the marker. After the agent's `rollback` or `bombadil undo` and before the restart, the next turn takes a restore point of the root that still holds the undone changes, and the next undo picks it, which silently replaces the first undo. Run with a fake list: an install turn (restore point 1), an undo turn (2) rolled back to 1, a second undo turn (3) rolled back to 2. With `bombadil undo` and a turn in between, the same happens. Two terminal undos in a row, with no turn between, roll back to the same restore point twice. A turn the limit refused at once leaves an empty restore point that only the launcher skips: `undo_last_turn` picks it, and `bombadil undo` or the agent's `rollback` then report a rollback that changes nothing | `launcher.py:633-665` against `snapshots.py:69-82`, `mcp_server.py:123-127`, `bin/bombadil:434-437` |
| A turn that changed nothing keeps its restore point, because the point is taken before anything is known about the turn, and nothing removes it afterwards except snapper's number cleanup, which deletes the oldest points past the limit of 30 whether or not their turns changed anything, so a run of turns that changed nothing pushes the restore points of earlier changes out. `Launcher._undo` picks the newest `turn:` point without asking whether the turn changed anything, so after a question the first `undo` rolls back to a point equal to the running root and says "Undone. System files go back to before “<the question>”"; the person has to ask again. Run through `AgentD` with a recording fake: one turn "tell me a joke", then `undo`, rolled back restore point 1. Only a turn the limit refused at once is skipped (`skip_restore_point`); the design drops every pair with no change at the end of the turn (Retention and pruning above) | `agentd.py:2093-2103`, `launcher.py:651-663` |
| An undo does not remove the restore points of the turns it took back. After an undo, a restart and a later turn, the first `undo` rolls back to that turn's restore point, and the next `undo` after it can pick the restore point of the turn already undone, so it reports taking that turn back while changing almost nothing. Run with the fake: rolled back 3, then 4, then 3. These points also count toward the limit of 30 | `launcher.py:651-663` |
| Before the restart, repeated undos alternate between rolling back and "Already undone", though no turn ran. The check for turns that ran since counts the restore points of turns an earlier undo took back, because undo removes none, and every rollback writes a marker without `covered`. Run with a fake of four restore points, no turn between and the boot id fixed: undo 1 rolled back to 4, undo 2 to 3, undo 3 said "Already undone" and rolled nothing back, undo 4 rolled back to 2, undo 5 said "Already undone" again. The person sees a sentence about turns since that is not true, and must ask twice to go back one turn | `launcher.py:641-663`; `tests/test_launcher.py` stops after two undos |
| The swap is two commands. If `btrfs subvolume snapshot` fails after `mv`, no `@` exists and the next boot has no root until someone renames the `@.undone-*` back. The only handler unmounts | `bombadil-rollback:5,10,14-15` |
| `snapshots = false` in `~/.config/bombadil/config.toml` is lost when the provider is chosen or changed, because `save_user` rewrites the file with only `provider`, `model`, `explain` and the `[models]` table. Run: the setting read back as `true` | `config.py:62-78`, called from `agentd.py:1586` and `bin/bombadil:448`. A third writer, `iso/airootfs/usr/local/bin/bombadil-setup:24` (the branch used when `agentd` is not running; it then ends with `systemctl --user restart bombadil-agentd`), overwrites the file with the `provider` line alone, which drops `model`, `explain` and `[models]` as well |
| No baseline restore point at install, so the first undo can only go back to the start of the first turn | `bombadil-install:64` creates the config and no snapshot |
| `rollback` called by the agent reports a failed swap as `CalledProcessError: Command ... returned non-zero exit status 1`; the script's own message ("no snapshot N") is captured and dropped. The launcher shows it, through `_reason` | `snapshots.py:66`, `mcp_server.py:205-206`, `launcher.py:426,906-916` |
| The restart that applies an undo starts a conversation with no memory of the earlier one, because `session_id` is kept only under the runtime directory (`agentd-state.json`), which a reboot clears. A restart of the daemon alone keeps it | `agentd.py:374-402`; [known issues](../known-issues.md) |
| `snapshots = false` reaches `agentd` only. On an installed system with it set, the agent's `snapshot`, `list_snapshots` and `rollback` tools and `bombadil undo` still work, because `OsTools` and the `undo` command build `snapshots.Snapshots()` without reading the config, while the launcher word answers "Undo is off here" | `mcp_server.py:42`, `bin/bombadil:435`, `agentd.py:2720`, `config.py:52` |
| `status.snapshots` is sent and no shell file reads it, so nothing on screen says whether restore points are being kept until `undo` is asked | `agentd.py:601`, no match in `shell/` |
| The UX brief asks that the `Undo` button not fade while the person looks away ([decisions](../design/ux-brief.md#decisions-i-picked-a-default-for)). A notice that waits makes the sticky closing line give way after its 12 seconds, and a warning replaces it at once, and the button goes with the line; the word `undo` still works. The desk's Now card builds "Undo is above the pill" from `changed` alone, so it does not know whether the button is still there | `shell/StatusLine.qml:68-70,212`, `shell/PillState.qml:299`, `shell/DeskState.qml:556`; `tests/test_notice_qml.py` asserts the button is gone while a notice has the line |

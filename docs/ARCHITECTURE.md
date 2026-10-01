# Architecture

## Decisions (2026-09-27)

| Choice | Pick | Why |
|---|---|---|
| Base | Arch + btrfs/snapper | Agents know it; rolling graphics stack; archiso; snapshots are the undo |
| Display | Hyprland now, custom compositor later | Slide-in special workspaces and IPC out of the box; a browser needs a real Wayland server |
| First target | QEMU VM, ISO also boots hardware | Fast loop while the core changes daily |
| Agent runtime | Official Claude Code / Codex CLIs wrapped by `agentd` | Logins and subscriptions just work; vendor tool use for free |
| Generated apps | QML via PySide6, `~/Apps/<name>/` | No build step, hot reload, real windows, no ports |
| Provider switching | One MCP server (`bombadil-os-mcp`) | Same OS abilities whatever the provider |
| Browser | Chromium with remote debugging on | Agent can drive it while you watch |

## One turn

1. The bar (or `bombadil ask`) writes `{"type":"prompt","text":…}` to the socket.
2. `agentd` takes a snapper snapshot named `turn:<n>: <prompt>`.
3. It runs the provider CLI once, in full-access mode, resuming the previous session id,
   with `bombadil-os-mcp` in its MCP config and `providers.system_prompt()` appended.
4. The CLI's JSON stream becomes `text` / `tool` / `result` events, broadcast to all clients.
5. The turn is appended to `~/.local/state/bombadil/turns.jsonl`.

"Undo that" is the agent calling `rollback` (or the user running `bombadil undo`):
`bombadil-rollback` swaps the last `turn:` snapshot in under the name `@`; it applies on
reboot. Home is its own subvolume and is not rolled back. The disk it works on is below.

## The installed disk (layout 1)

`bombadil-install` writes the one thing no update can change later, so it is written once and
recorded in `/var/lib/bombadil/install.json` (`"layout": 1`).

| Where | What |
|---|---|
| p1, 1 GiB, FAT32, `/efi` | GRUB's core and modules, `grub.cfg`, `grubenv`, and the fallback path `EFI/BOOT/BOOTX64.EFI`. Never rolled back. |
| p2 | btrfs, inside LUKS2 when a password was given. Top-level subvolumes: `@` (`/`, with `/boot`), `@home`, `@snapshots` (`/.snapshots`), `@log` (`/var/log`), `@pkg` (`/var/cache/pacman/pkg`). |
| `/boot` | Inside `@`: `vmlinuz-linux`, `initramfs-linux.img` and `initramfs-linux-fallback.img`, so a restore point holds the kernel that goes with its modules and an undo cannot start a kernel whose modules are gone. |
| `@home` | Nested subvolumes that a restore point of the home folder skips: `.cache`, `.local/state/bombadil`, `.claude`, `.codex`, `.local/share/claude`, `.config/chromium`, `.local/share/bombadil/browser`, `Projects`. |

`@log` and `@pkg` sit beside `@` because undo replaces `@`: the logs of a bad start, and the
packages downloaded since, survive the undo. `/etc/fstab` names the disks by identifier and the
system by subvolume, so a snapshot swapped in under the name `@` mounts like the one it replaced.
GRUB reads the kernel from `/@/boot`, so `grub.cfg` stays right after any undo.

snapper has two configs, `root` and `home`, with no hourly snapshots (one is taken per agent
turn) and the newest 30 kept by `snapper-cleanup.timer`.

**Encryption.** With a password the disk is LUKS2 with three ways in: the password (argon2id at
256 MiB, the cost GRUB can pay on firmware that fragments low memory; typed once, at GRUB), a
random key file inside the initramfs (so the initramfs opens the disk without asking again),
and a recovery key from `systemd-cryptenroll`, kept in `/var/lib/bombadil/recovery-key`
(mode 600, inside the encrypted disk) until the first start shows it once. The same password is
the account's. The initramfs is systemd's (`sd-encrypt`, `/etc/crypttab` with `x-initrd.attach`).

**The plan.** The install card (a later piece) writes a JSON plan and gives the password on a
file descriptor, never in a file, an argument or the log: `bombadil-install --plan FILE
--password-fd N`. `src/bombadil/installplan.py` checks every field before anything is touched
(disk, mode, time zone and where it came from, keymap, computer name, what to carry, whether to
encrypt, internal or USB) and is also the place `install/carry.list` is read: the home paths that
may come along from the stick (the sign-in, the chosen AI, made apps, the browser profile).
`bombadil-install DISK --yes` is the plan-less form the tests and the VM helper scripts use: no password,
no encryption.

**Refresh** (`bombadil-install --refresh DISK`) gives a disk a new system under the home folder
that is already there. Everything that can be read is read first, so a disk that is not a
Bombadil disk, or has Windows on it, is left exactly as it was. Then the old `@` and `@snapshots`
are renamed `@.before-refresh-<stamp>` and `@snapshots.@.before-refresh-<stamp>` (so an old
restore point can never be swapped into the new system), the old kernel is put in that system's
own `/boot` and its fstab pointed at `/efi`, and the EFI partition is emptied, not formatted
again, so its identifier stays. `@home` is kept and only gains the nested subvolumes above.
Kept from the old system: the computer's name, time zone, locale, console keymap, machine id,
Wi-Fi networks, SSH host keys, the account's password, and the list of programs it had that the
new image does not (`/var/lib/bombadil/refresh-<stamp>.txt`, for the agent to offer back). What
is carried from the stick never replaces what the home folder already has.
`bombadil-rollback --device DEV refresh` puts the old system back (the newest one that is whole). It is
kept until a later cleanup removes it after 14 days.

A refresh changes things in an order that makes a stop harmless. Reading comes first (and a disk with
less than 10 GB free is refused, since the old system stays beside the new one). The old system is then
renamed aside and the new one copied in; until the boot files are replaced, which is the very last step,
a stop puts the old system straight back and says so. A stop after that leaves `var/lib/bombadil/install-incomplete`
in the half-made system, and running the refresh again starts over from the system that is still aside.
Undo itself never leaves the disk without an `@`: the snapshot is made beside it first, and the two names
are exchanged in one step.

Tested by `tests/test_installer.py` (the decisions, as sourced shell functions) and, in a VM, by
`scripts/test-vm.sh` with `MODE=install`, `install-encrypted` and `refresh`.

## Signing in

agentd never handles a password or a token: it runs the provider CLI's own login
(`claude auth login`, `codex login`) in a terminal it holds, so the CLI stores its
credentials exactly as it would in a terminal of yours (`signin.py`).

1. Setup states, shown in the pill with chips: `choose` (first boot: Claude or Codex),
   `checking`, `signed_out`, `offline`, `signing_in`, `ready`. Prompts wait until `ready`
   and then run; a `!command` runs anyway.
2. The CLI runs with `BROWSER=bombadil-browser`, which hands its page to agentd over the
   socket, tagged with the sign-in's id. The page opens in the browser panel's Chromium
   (own profile under `~/.local/share/bombadil/browser`, no first-run pages, DevTools on
   127.0.0.1:9222) and comes back to the CLI's localhost callback.
3. If `$BROWSER` is not used within 2 s, the printed URL opens instead. A Claude page that
   ends on the code page gets `code#state` typed into the CLI, read from the tab's address.
4. DevTools and Hyprland tell it when the panel slid out or the browser was closed; the
   pill offers the page again. Esc cancels (the pill says "Cancelling" at once, and a page
   still opening in a slow browser is dropped, never slid in afterwards); 10 minutes without
   an end times out (`BOMBADIL_SIGNIN_TIMEOUT`). Success is confirmed with `claude auth
   status` or `codex login status`. A browser that cannot open the page is an error the pill
   shows with "Open it again", not a quiet success.
5. No internet (no TCP connection to the provider's sign-in host): the pill says so, offers
   Wi-Fi, and the sign-in starts once the host answers.
6. A turn whose CLI says the login is gone (`authentication_failed`, a 401) signs in again
   and runs the prompt once more, with what you did meanwhile told to the model. If the
   rerun says it too, the login is not what is wrong (a 403, an API key in the environment):
   the CLI's own words show and there is no second sign-in. Only the provider whose turn
   failed is signed in again, even if you switched AI meanwhile. A `/login` typed in a
   terminal lands in the panel too, and agentd watches for it to finish.
7. "Sign in" typed while signed in starts a new login, except for a provider whose login
   signs the stored one out as it starts (`login_replaces`: Codex, even if the new one is
   then called off): there it answers "already signed in".

`fake_signin.py` plays a provider login on localhost for the tests and the VM smoke test
(`BOMBADIL_PROVIDER=fake BOMBADIL_FAKE_SIGNIN=auto|manual|never|fail`).

## Generated apps

`create_app` writes `main.qml`, optional `app.py` (a `Backend(QObject)` exposed as
`backend`), `app.toml` and a `.desktop` entry, checks the app offscreen (errors with
`file:line` plus a screenshot go back to the agent), then starts `bombadil-app run <name>`.
The runtime owns the window and reloads the QML into it on every write, so the agent
iterates by calling `create_app` again and the app keeps its place, size and saved state.
Each app lives in its own Hyprland special workspace and gets a chip in the bar.

`import Bombadil` is the app kit (`share/qml/Bombadil`, native types in
`src/bombadil/appkit/native`), and the `bombadil-apps` skill in `share/skills` tells the
agent how to use it. The skill reaches both CLIs from `/etc/skel` (`~/.claude/skills` and
`~/.agents/skills`) and through the `app_guide` tool.

## Why lines and pictures

Nothing here asks the model again. `narrate.py` keeps the sentence the agent wrote before each
step (`because`) and what the turn read from outside (`after`); the status line shows both on
hover. A bare "why" during a turn is answered from that record.

`show_card` (the agent) and `system_map` (the machine itself) hand `agentd` a `diagram` card
over its socket (`cards.py` checks and lays it out, `sysmap.py` captures the network, boot, one
service, disks, sound or screens from the real machine in parallel, under half a second; the boot
record and the check that the provider answers get longer, since both are slow by nature). `agentd`
broadcasts it, `shell/CardHost.qml` draws it above the status line with the kit's `Diagram`, and the
agent gets the same picture back in words. A card still being written streams in a box at a time;
a turn that changed a part of the machine it touched ends with a before/after receipt. A click on a
box that names a file, service, package, page or turn comes back as `{"type":"open"}`; a service,
package, folder or text file opens in the details drawer with `bombadil view` (`pager.py`: Esc
closes it, the arrows and wheel scroll), and the line says "Showing" only once the drawer's window
was there. A picture that cannot be drawn takes the last one away. (How each picture reads the
machine, and what a real VM taught us, is in [pictures.md](pictures.md).)
The card host is loaded through a `Loader`, so a picture that will not draw costs the pictures, never
the bar. (The kit reaches the shell through `bin/bombadil-shell`'s import path: Quickshell cannot
import from outside its own folder any other way.)

## Next

- Boot the ISO in QEMU and fix what the real Hyprland session shows (bar layering,
  app window rules, greetd autologin).
- Browser control: a Playwright/CDP MCP against Chromium's port 9222, so the agent can
  read and act in pages the user is watching.
- Voice input and a screen-aware mode (periodic screenshots into context).
- Custom compositor once the interaction model is settled.

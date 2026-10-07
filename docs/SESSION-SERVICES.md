# The session stays alive

Bombadil's interface is the pill. If the daemon behind it, the bar that draws it or the
notification daemon dies, the person is left with a desktop and no way to ask for anything. So the
session is built to put itself back together without anyone's help, and the rest of the system
(the agent, the installer, the update path) is built around that.

## The three user services

| Unit | What it runs | Why it is a unit |
|---|---|---|
| `bombadil-agentd.service` | the agent daemon (`agentd`) | systemd starts it again if it dies, in a clean environment |
| `bombadil-shell.service` | the bar and pill, through the `bombadil-shell` wrapper | the wrapper puts the app kit's QML module on Quickshell's import path, which a bare `quickshell -p` cannot do |
| `mako.service` (a drop-in) | notifications | the package's unit is kept; the drop-in adds the restart policy |

All three restart on exit: `Restart=always`, `RestartSec=1`. `StartLimitIntervalSec=0` sits in
the `[Unit]` section, which is the only place systemd reads it: under `[Service]` it is ignored and
the default of five starts in ten seconds would end a crash loop with no pill at all. A daemon that
keeps failing is still better than none.

`agentd` runs with `KillMode=process`: when only the daemon restarts, the apps and the browser it
started keep running. Stopping the unit ends the daemon, nothing else.

## Who starts them

Hyprland's start hook (`iso/airootfs/etc/skel/.config/hypr/hyprland.lua`) hands the session's
environment to the user manager and then restarts the units:

```lua
hl.on("hyprland.start", function()
    hl.exec_cmd("systemctl --user import-environment && systemctl --user restart bombadil-agentd bombadil-shell mako")
end)
```

`WAYLAND_DISPLAY` and the compositor's signature exist only inside the session, not in the user
manager, so the import comes first. Nothing else starts the daemon, the bar or `mako`, so no second
copy can race the first.

| Key | Does |
|---|---|
| Super (tap) or Alt+Space | the pill takes the keyboard; again gives it back |
| Super+Esc | stops the running turn and everything it started |
| Super+Ctrl+Esc | `systemctl --user restart bombadil-shell`, for a bar that hangs |

Alt+Space exists for a machine whose host keeps the Super key (a virtual machine's window on
Windows opens the host's Start menu).

Setup and the terminal client's hints restart the daemon through systemd too (`systemctl --user
restart bombadil-agentd`), so a settings change and a crash take the same path.

## Restore points do not pile up

Every turn starts with a btrfs snapshot (the undo button). They are created under snapper's
`number` cleanup algorithm, and the installer sets `TIMELINE_CREATE=no NUMBER_CLEANUP=yes
NUMBER_LIMIT=30` and enables `snapper-cleanup.timer`. The newest 30 survive; older ones go. Without
the algorithm on each snapshot nothing would ever delete them and the disk would fill.

## The installer

`bombadil-install` copies the running system to a btrfs disk. Two rules came out of real boots:

- **The kernel comes from the copied system**, `/usr/lib/modules/<newest>/vmlinuz`, never from the
  boot medium. With `copytoram` (a stick on a machine with RAM to spare) the medium is unmounted
  before the kernel is copied; reading it there stopped the script after the disk had been wiped
  and copied, and left an installed system with no kernel for the boot loader. The lookup now happens
  before the disk is touched, so a missing kernel stops the install with the disk intact.
- **Software is installed with `pacman -Syu --noconfirm --needed <packages>`**, never `-Sy` alone
  (Arch does not support a partial upgrade). A full upgrade is not silent: the status line says
  "Updating the system and installing X", and the agent is told to say so in one sentence first.

## Models

Bombadil's own turns run on Sonnet by default for Claude (`config.DEFAULT_MODELS`), a per-provider
setting. The launcher knows two words, "use opus" and "use sonnet", which
switch the model for the session. Only a model the person wrote in the config is saved: picking a
provider never writes the default into the file, so a later change of default reaches everyone.

## Updating a running system

Files under `/etc/skel` are copied into a home directory once, when it is created. A change to
`hyprland.lua` therefore does not reach a system that already exists: updating in place means
copying the new `hyprland.lua` to `~/.config/hypr/` as well as the units to `/etc/systemd/user/`.
The new binaries (`bin/`) and sources (`src/`, `shell/`, `share/`) are in `/usr/share/bombadil`,
with the commands symlinked from `/usr/local/bin`.

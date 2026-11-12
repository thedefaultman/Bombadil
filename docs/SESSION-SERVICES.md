# The session stays alive

Bombadil's interface is the pill. If the daemon behind it, the bar that draws it or the
notification daemon dies, the person is left with a desktop and no way to ask for anything. So the
session is built to put itself back together without anyone's help, and the rest of the system
(the agent, the installer, the update path) is built around that.

## The two user services

| Unit | What it runs | Why it is a unit |
|---|---|---|
| `bombadil-agentd.service` | the agent daemon (`agentd`) | systemd starts it again if it dies, in a clean environment |
| `bombadil-shell.service` | the bar and pill, through the `bombadil-shell` wrapper | the wrapper puts the app kit's QML module on Quickshell's import path, which a bare `quickshell -p` cannot do |

Both restart on exit: `Restart=always`, `RestartSec=1`. `StartLimitIntervalSec=0` sits in
the `[Unit]` section, which is the only place systemd reads it: under `[Service]` it is ignored and
the default of five starts in ten seconds would end a crash loop with no pill at all. A daemon that
keeps failing is still better than none.

`agentd` runs with `KillMode=process`: when only the daemon restarts, the apps and the browser it
started keep running. Stopping the unit ends the daemon, nothing else.

## Who starts them

Hyprland's start hook (in Bombadil's part of the config, `share/hypr/bombadil.lua`, which is
`/usr/share/bombadil/share/hypr/bombadil.lua` on a machine) hands the session's
environment to the user manager and then restarts the units:

```lua
hl.on("hyprland.start", function()
    hl.exec_cmd("systemctl --user import-environment && systemctl --user restart bombadil-agentd bombadil-shell")
end)
```

`WAYLAND_DISPLAY` and the compositor's signature exist only inside the session, not in the user
manager, so the import comes first. Nothing else starts the daemon or the bar, so no second
copy can race the first. (The home's own `~/.config/hypr/hyprland.lua` is a short stub that loads
that file. If the file is missing, the stub runs the old whole config kept beside it as
`hyprland.lua.before-split` and stops there, and with none of that it has the same `systemctl` line
of its own, so the pill and the bar still start.) Notifications are the bar's own: its
`NotificationServer` owns `org.freedesktop.Notifications`, and no notification daemon is installed.

| Key | Does |
|---|---|
| Super (tap) or Alt+Space | the pill takes the keyboard; again gives it back |
| Super+Esc | stops the running turn and everything it started |
| Super+Return | a terminal on the desktop (`bombadil terminal`): a drawer that shows (details, files, an app) is put away first, because a window opened while it shows would stay inside it, and the terminal is started without Hyprland's `HL_INITIAL_WORKSPACE_TOKEN`, which would otherwise still send it to the drawer that showed when the keys went down |
| Super+Ctrl+Esc | `systemctl --user restart bombadil-shell`, for a bar that hangs |

Alt+Space exists for a machine whose host keeps the Super key (a virtual machine's window on
Windows opens the host's Start menu).

**How a tap reaches the pill.** The tap and Alt+Space are Hyprland *global shortcuts* answered inside
the bar's own process. `share/hypr/bombadil.lua` binds a release of Super to `hl.dsp.global("bombadil:pill-release")`
and Alt+Space to `bombadil:pill-press`; `shell/PillKeys.qml` registers a `GlobalShortcut` with each of
those names and calls `PillState.summon()`, the same function a bar message from `bombadil pill` calls.
They used to run `bombadil pill`, a Python start and a round trip through the daemon, and on a loaded
machine the first letters typed after the tap went to the window underneath before the pill had the
keyboard. Two things to know when changing it:

- A name says which edge the bar listens to, and Hyprland does nothing, and says nothing, for a name no
  client registered. There is no fallback on purpose (a fallback that also ran `bombadil pill` would be
  the slow path again). The guard is the VM smoke: `pill-shortcuts-registered` asks `hyprctl
  globalshortcuts` for both names before a key depends on them, and a tap that did nothing prints the
  list in its `DIAG` line. `tests/test_iso_profile.py` holds the names in the lua and in the QML together.
- The bar makes each shortcut once. The protocol refuses a second registration of the same appid and
  name and the client that comes second crashes, so `PillKeys.qml` is not made per screen.

`bombadil pill` still works, for scripts and for a session without Hyprland (the headless desktop test).
These binds are in Bombadil's part of the config, which comes with the package, so an update changes
them on a machine that already exists; Hyprland reads them at its next start. The home's own
`~/.config/hypr/hyprland.lua` is a stub that loads that part and then holds the person's own lines.
A home that still has the whole old file (from before the config was split) is fixed the first time
agentd starts after the update: homefix keeps the old file whole as `hyprland.lua.before-split` and
writes the stub with the person's own lines under it (`docs/INSTALLED-OS.md`, "The Hyprland config").
Until then a tap keeps running `bombadil pill` and Super+Return keeps running plain `foot`, as they did.

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

An update is a newer `bombadil` package, taken with `pacman -Syu`. It replaces the commands (`bin/`),
the sources (`src/`, `shell/`, `share/`, which include Bombadil's part of the Hyprland config) in
`/usr/share/bombadil`, and the units in `/usr/lib/systemd`; each command is a link from `/usr/bin`
into that tree. The package cannot ship a file that another package owns, and it does not write into
a home. Numbered migrate steps (run by a pacman hook) change the first kind, and numbered homefix
steps (run by agentd at start) the second. Files under `/etc/skel` are copied into a home once, when
it is created; what a new release adds to a home that exists is a homefix step's job.

Nothing restarts when the package is replaced. `bombadil-agentd` and `bombadil-shell` keep the code
they loaded until the next sign-in, when the start hook above restarts them (or until
`systemctl --user restart bombadil-agentd bombadil-shell`). `docs/INSTALLED-OS.md`, "Packages and
updates", has the whole story.

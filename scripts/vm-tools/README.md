# Driving the laptop VM from WSL

Tools for looking at and working on the installed Bombadil VM that `scripts/wsl-vm.sh` runs (QEMU
with KVM inside the WSL distro `bombadil`). They talk to it without a window: keys, clicks and
screenshots over QMP, a shell over the serial console. Run them inside WSL as root. The VM's files
live in `$BOMBADIL_TREE/out/vm` (`/root/Bombadil/out/vm`); set `VMDIR` to point at another VM.

| tool | what it does |
| --- | --- |
| `serialpump` | keep running in the background: keeps the serial log flowing and types what the other tools write to a FIFO (`/run/vmserial.in`) |
| `vmlogin` | log in on the serial console as `user` (empty password) whatever the prompt is |
| `vmsh CMD` | run a command in the guest and print what it printed (`T=` seconds to wait) |
| `vmpy < script.py` | run a python script in the guest (base64 over serial; arguments are single words) |
| `vmin` | `keys meta_l+ret`, `type TEXT`, `click X Y`, `hover X Y`, `shot out.png` over QMP |
| `vmsmoke` | run the ISO's `bombadil-smoke` inside the running VM, pressing the keys and taking the screenshots it asks for; it refuses on a VM with a provider chosen (its sign-in checks restart agentd with stand-ins and pick providers; `FORCE=1` on a scratch copy) |
| `vmlink up\|down` | pull the VM's network cable, to see what a person sees offline |
| `vmwatch` | a screenshot whenever the screen changed, in `out/vm/shots` |
| `probe PROMPT SECS [STOP_AT]` | send a prompt to agentd's socket and print every event with its time (status lines, cards, tools, results) |
| `scratch-up`, `with-scratch TOOL` | a headless scratch copy of the disk, and any tool above pointed at it |
| `stonecrop OUT.png N SECS [X Y W H] [SCALE]` | N frames of the stone in the pill from the VM's own screen (QMP screendumps), scaled up in a row: its faces and motion at real size |
| `lifttrace OUT SECS INTERVAL` | the stone's and the card's top edge about ten times a second, to see whether the pill moves when a card changes size |
| `scratch_api.py` | a scripted Anthropic API for the real `claude` CLI: hangs, slow tools, long thinking, `show_card` calls, a service restart, a volume change; no model, no quota, no login |

Launcher commands (`scripts/wsl-vm.sh`, `bombadil-vm.cmd`):

- no argument boots the disk, and `stop` shuts it down cleanly (ACPI power button).
- `update` builds the package from this checkout, serves it on `127.0.0.1:8123` while the VM runs (the
  guest's `/etc/pacman.d/bombadil.conf` tries `10.0.2.2:8123` first, which it does on a build `wsl-vm.sh`
  made: it sets `BOMBADIL_DEV_SERVER=1` for the ISO and for this package, and a release has no such line)
  and boots the disk. Saying "update" to the agent there installs it with pacman, and nothing on the disk
  is replaced. It refuses a disk installed before Bombadil was a package. This mode and `refresh` (now the
  installer's own) are tested here only against stand-ins (`tests/test_vm_harness.py`) and have not yet been
  run on a real WSL machine.
- `refresh` stops the VM, takes a host-side `qemu-img` restore point of the disk and runs the installer's own
  refresh from this checkout's ISO: the system is replaced, the home folder (login, apps and files) stays,
  and the old system is kept aside on the disk.
- `reinstall` wipes the disk and asks first (the old disk is kept as `bombadil.qcow2.before-reinstall`).

`run-vm.sh --disk` refuses to attach a disk that already holds a system to the ISO, because the ISO's boot
menu has an entry that erases `/dev/vda` without asking.

Start: `python3 scripts/vm-tools/serialpump &` once per boot of the VM, then `scripts/vm-tools/vmlogin`.
After a reboot the serial console needs `vmlogin` again (send `user`, wait for `Password:`, send an
empty line; the script does it).

## Things that bit

- **Never run a model turn on a copy of the disk.** It carries the real Claude login; the copy's CLI
  refreshes the OAuth token, tokens rotate, and the real VM can be left with a dead refresh token.
  On a scratch copy use launcher words and `!` commands, or start agentd with
  `ANTHROPIC_BASE_URL=http://10.0.2.2:18555 ANTHROPIC_API_KEY=sk-ant-fake` against `scratch_api.py`.
  (A launcher phrase it does not know, such as "volume up", goes to the model.)
- **Restart agentd** (nothing else does) with `hyprctl dispatch 'hl.dsp.exec_cmd("agentd")'`; the old
  `hyprctl dispatch exec agentd` is a Lua error on Hyprland 0.56.
- **The serial pump's XON keeps GRUB's menu waiting** on an install that still has the menu style
  (press Enter with `vmin keys ret`). With the hidden menu it boots by itself.
- **What an update does not do**: it replaces the package's files and runs its migrate steps, and nothing
  that is running restarts. Sign out and in (or `systemctl --user restart bombadil-agentd bombadil-shell`) to
  run the new agentd and bar. GRUB's menu is not regenerated: after a change to GRUB's defaults in
  `/etc/default/grub` run `sudo bombadil-grub-config` (it runs `grub-mkconfig` the way the installer does, in
  the C locale and with the "Loading ..." lines cut; do not run `grub-mkconfig` itself on a layout-1 disk, see
  docs/architecture/installed-os.md, "What GRUB shows"). The person's own files stay theirs:
  `~/.config/hypr/hyprland.lua` loads Bombadil's part from the package, and a line they or the launcher put
  under it, such as the `Virtual-1` pin, stays (the smoke's `virtual-display-size` fails only for a pin narrower than
  1280 px).
- **Windows**: the Windows key opens the Start menu, not the pill (use Alt+Space once it is on the
  VM); the window opens small, Ctrl+Alt+F makes it full screen and again leaves it; do not resize or
  minimise the window from Windows scripts (it went black, then full screen). If the window never
  shows and `wslg/stderr.log` under `/mnt` says `msrdc.exe ... exited` every 31 s, WSLg's window link is
  stuck: power the guest off (`vmsh 'sudo systemctl poweroff'`), run `wsl --shutdown` in Windows,
  start the VM again.

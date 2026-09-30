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
| `update-in-place [--reboot] REF` | move the installed VM to the Bombadil in a git ref without reinstalling, so the login on its disk stays |
| `scratch-up`, `with-scratch TOOL` | a headless scratch copy of the disk, and any tool above pointed at it |
| `scratch_api.py` | a scripted Anthropic API for the real `claude` CLI: hangs, slow tools, long thinking, `show_card` calls; no model, no quota, no login |

Launcher commands (`scripts/wsl-vm.sh`, `bombadil-vm.cmd`): no argument boots the disk, `stop` shuts it
down cleanly (ACPI power button), `refresh` stops it, takes a host-side `qemu-img` restore point of the
disk and moves it to this checkout in place (login, apps and files stay), `reinstall` wipes the disk
and asks first (the old disk is kept as `bombadil.qcow2.before-reinstall`). `run-vm.sh --disk` refuses
to attach a disk that already holds a system to the ISO, because the ISO's boot menu has an entry
that erases `/dev/vda` without asking.

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
- **What `update-in-place` does not move**: `iso/` changes. By hand: packages with
  `sudo pacman -Syu --noconfirm --needed ...`; files under `/etc`; GRUB defaults in `/etc/default/grub`
  then `sudo grub-mkconfig -o /efi/grub/grub.cfg` (`/boot/grub/grub.cfg` on a disk installed before layout 1, see docs/ARCHITECTURE.md); and the user's `~/.config/hypr/hyprland.lua`,
  which also keeps the launcher's `Virtual-1` pin (so the smoke's `virtual-display-size` fails here).
- **Windows**: the Windows key opens the Start menu, not the pill (use Alt+Space once it is on the
  VM); the window opens small, Ctrl+Alt+F makes it full screen and again leaves it; do not resize or
  minimise the window from Windows scripts (it went black, then full screen). If the window never
  shows and `/mnt/wslg/stderr.log` says `msrdc.exe ... exited` every 31 s, WSLg's window link is
  stuck: power the guest off (`vmsh 'sudo systemctl poweroff'`), run `wsl --shutdown` in Windows,
  start the VM again.

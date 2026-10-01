#!/usr/bin/env bash
# Build, install and boot Bombadil in a VM window from WSL. scripts\bombadil-vm.cmd runs this as
# root in a WSL distro named "bombadil"; it also works on any Arch host. It builds the commit this
# checkout has checked out, in a clone on a Linux filesystem (a Windows checkout loses symlinks and
# exec bits), and the VM's disk lives in that clone's out/.
#   wsl-vm.sh            boot the installed disk; the first time, build the ISO and install it
#   wsl-vm.sh live       boot the live ISO (the disk is not touched)
#   wsl-vm.sh refresh    keep the disk and everything on it (the Claude login, apps, files): stop the VM, take
#                        a qemu-img restore point of the disk, boot it and move it to this checkout in place
#   wsl-vm.sh reinstall  rebuild the ISO if the checkout changed it, then wipe the disk and install; it asks
#                        first (--yes or BOMBADIL_YES=1 skips that), and keeps the old disk as
#                        bombadil.qcow2.before-reinstall
#   wsl-vm.sh build      only build the ISO
#   wsl-vm.sh stop       shut the running VM down cleanly (the ACPI power button), or kill it if it will not
# Tools to drive a running VM without a window (keys, shell, smoke checks, in-place updates): vm-tools/.
# MEM (default 5G), SMP (4), RES (1600x900) and GL (0) go to run-vm.sh; RES is also pinned in the
# installed system. WSLg's host OpenGL is often llvmpipe, and then the guest rendering in software
# itself is the faster of the two.
set -euo pipefail
[[ $EUID == 0 ]] || { echo "run as root (wsl -d bombadil -u root ...)"; exit 2; }
src="$(cd "$(dirname "$0")/.." && pwd)"
tree="${BOMBADIL_TREE:-/root/Bombadil}"
out="$tree/out"; vm="$out/vm"; disk="$out/bombadil.qcow2"
export MEM="${MEM:-5G}" SMP="${SMP:-4}" GL="${GL:-0}" RES="${RES:-1600x900}"

setup() {
  local pkgs=(archiso qemu-desktop edk2-ovmf git nodejs npm python)
  if ! pacman -Q "${pkgs[@]}" >/dev/null 2>&1; then
    pacman-key --init && pacman-key --populate archlinux
    pacman -Syu --noconfirm --needed "${pkgs[@]}"
  fi
  local kvm=kvm_amd; grep -qw vmx /proc/cpuinfo && kvm=kvm_intel
  [[ -e /dev/kvm ]] || modprobe "$kvm"
  echo "$kvm" > /etc/modules-load.d/kvm.conf
  git config --global --get-all safe.directory | grep -qxF '*' || git config --global --add safe.directory '*'
}

sync_tree() {
  [[ "$src" -ef "$tree" ]] && return
  [[ -d "$tree/.git" ]] || git clone -q "$src" "$tree"
  git -C "$tree" fetch -q "$src" HEAD
  git -C "$tree" checkout -q --detach FETCH_HEAD
}

# What goes into the ISO, so a commit that only touches docs or VM scripts reuses the last build.
iso_key() { git -C "$tree" rev-parse HEAD:bin HEAD:src HEAD:shell HEAD:share HEAD:iso HEAD:install HEAD:scripts/build-iso.sh | sha1sum | cut -c1-12; }

build() {
  local key; key=$(iso_key)
  if [[ -f "$out/.iso-key" && "$(cat "$out/.iso-key")" == "$key" ]] && ls "$out"/*.iso >/dev/null 2>&1; then return; fi
  echo "Building the ISO for $(git -C "$tree" log -1 --format='%h %s') (15 to 30 minutes)..."
  rm -f "$out"/*.iso "$out/.iso-key"
  (cd "$tree" && SOURCE_DATE_EPOCH=$(git log -1 --format=%ct) BOMBADIL_TEST_ENTRIES=1 WORK=/var/tmp/bombadil-work scripts/build-iso.sh)
  rm -rf /var/tmp/bombadil-work   # several GB of build tree; pacman's package cache stays for next time
  echo "$key" > "$out/.iso-key"
}

wait_log() {  # wait_log FILE PATTERN SECONDS PID
  for _ in $(seq "$3"); do
    grep -aqE "$2" "$1" && return 0
    kill -0 "$4" 2>/dev/null || break
    sleep 1
  done
  echo "gave up waiting for '$2'; see $1"; kill "$4" 2>/dev/null; exit 1
}

# A reinstall deletes the disk and with it the Claude login, the apps and everything made since.
confirm_wipe() {
  [[ -f "$disk" ]] || return 0
  [[ "${BOMBADIL_YES:-}" == 1 ]] && return 0
  echo "Reinstalling wipes the VM's disk: the Claude login, the apps and the files on it." >&2
  echo "The old disk is kept as bombadil.qcow2.before-reinstall until the next reinstall. 'refresh' keeps everything." >&2
  [[ -t 0 ]] || { echo "No terminal to ask on; set BOMBADIL_YES=1 to go on." >&2; exit 1; }
  local a; read -r -p "Type wipe to go on: " a
  [[ "$a" == wipe ]] || { echo "Left the disk as it is." >&2; exit 1; }
}

# Install onto a fresh disk without a window: boot the ISO's serial-console entry with the smoke
# test edited off its command line, which leaves a root shell on the serial port, and run
# bombadil-install there. The installed system keeps a serial console (a login for "user").
install() {
  build
  confirm_wipe
  local iso; iso=$(ls -t "$out"/*.iso | head -1)
  local log="$vm/install.log" qmp="$vm/install.qmp" sock="$vm/install.sock"
  mkdir -p "$vm"; : > "$log"; rm -f "$qmp" "$sock" "$out/.installed"
  [[ -f "$disk" ]] && mv -f "$disk" "$disk.before-reinstall"
  qemu-img create -q -f qcow2 "$disk" 40G
  echo "Installing onto the VM's disk (a few minutes, no window)..."
  qemu-system-x86_64 -enable-kvm -cpu host -m "$MEM" -smp "$SMP" -display none -device virtio-vga \
    -bios /usr/share/edk2/x64/OVMF.4m.fd -cdrom "$iso" -boot d -no-reboot \
    -drive file="$disk",if=virtio,format=qcow2 -netdev user,id=n0 -device virtio-net,netdev=n0 \
    -qmp "unix:$qmp,server,nowait" \
    -chardev "socket,id=s0,path=$sock,server=on,wait=off,logfile=$log" -serial chardev:s0 &
  local pid=$!
  wait_log "$log" "Boot in" 120 $pid
  local keys=(down e end); for _ in $(seq 15); do keys+=(backspace); done   # " bombadil.smoke"
  python3 "$tree/scripts/qmp.py" "$qmp" send-keys "${keys[@]}" ret
  wait_log "$log" 'root@[^ ]+ [^ ]+\]#' 300 $pid
  # Then pin the screen to the VM window's size: QEMU's GTK window tells the guest 640x480 before
  # it grows, so Hyprland's "preferred" mode would be 640x480.
  # Typed a few bytes at a time and the socket held open: QEMU drops whatever the UART hasn't
  # taken yet when the client hangs up. The echo is split so the typed line never matches it.
  python3 - "$sock" "$RES" <<'PY'
import socket, sys, time
s = socket.socket(socket.AF_UNIX); s.connect(sys.argv[1])
monitor = f'hl.monitor({{ output = "Virtual-1", mode = "{sys.argv[2]}@60", position = "0x0", scale = 1 }})'
# The installer leaves the new system mounted for this, and unmounts it afterwards.
line = ("BOMBADIL_INSTALL_LEAVE_MOUNTED=1 BOMBADIL_INSTALL_CMDLINE='console=tty0 console=ttyS0,115200' bombadil-install /dev/vda --yes"
        f" && echo '{monitor} -- the VM window (scripts/wsl-vm.sh)' >> /mnt/home/user/.config/hypr/hyprland.lua"
        " && sync && umount -R /mnt && echo INSTALL-''DONE; poweroff\r").encode()
for i in range(0, len(line), 8):
    s.sendall(line[i:i + 8]); time.sleep(0.05)
time.sleep(1)
PY
  wait $pid || true
  grep -aq "INSTALL-DONE" "$log" || { echo "install failed; see $log"; exit 1; }
  { iso_key; git -C "$tree" log -1 --format='%h %s'; } > "$out/.installed"
  echo "Installed Bombadil ($(sed -n 2p "$out/.installed")) onto $disk."
}

# After a sleep or resume WSLg's window link can stick: msrdc exits at once and is started again every
# 31 s, and the VM runs with no window on Windows. A `wsl --shutdown` (after stopping the VM) fixes it.
wslg_check() {
  local log=/mnt/wslg/stderr.log now n
  [[ -r "$log" ]] || return 0
  now=$(date +%s)
  n=$(grep -a 'exited.*msrdc.exe' "$log" | tail -4 | sed -E 's/^\[([0-9:]+)\..*/\1/' | while read -r t; do
        s=$(date -d "$t" +%s 2>/dev/null) || continue; (( now - s >= 0 && now - s < 100 )) && echo x
      done | wc -l)
  if (( n >= 3 )); then
    echo "Warning: WSLg's window connection looks stuck (msrdc keeps exiting), so the VM window may not appear." >&2
    echo "If it does not: stop this (Ctrl+C), run 'wsl --shutdown' in Windows, then start the VM again." >&2
  fi
  return 0
}

run() {  # run-vm.sh, with QEMU's own messages kept in out/vm/qemu.log
  mkdir -p "$vm"; wslg_check
  echo "In the window: Ctrl+Alt+F toggles full screen. The Windows key opens Windows' Start menu; Alt+Space opens the pill where this build has it." >&2
  "$tree/scripts/run-vm.sh" "$@" 2>&1 | tee "$vm/qemu.log"
}

stop() {  # the ACPI power button (the guest shuts down and closes its disk), then QEMU exits
  pgrep -f "qemu-system-x86_64.*-name Bombadil " >/dev/null || { echo "The VM is not running."; return 0; }
  python3 "$tree/scripts/qmp.py" "$vm/qmp.sock" powerdown 2>/dev/null || true
  for _ in $(seq 60); do pgrep -f "qemu-system-x86_64.*-name Bombadil " >/dev/null || { echo "The VM is off."; return 0; }; sleep 1; done
  echo "It did not shut down in 60 s; stopping QEMU."; pkill -f "qemu-system-x86_64.*-name Bombadil " || true
}

# Keep the disk: a host-side restore point, then the installed Bombadil moves to this checkout in
# place (vm-tools/update-in-place: packages, GRUB and home files are not part of it). Until the
# installer has a refresh mode of its own this is the whole refresh.
refresh() {
  [[ -f "$out/.installed" && -f "$disk" ]] || { echo "Nothing to refresh: there is no installed disk yet. Run without arguments first." >&2; exit 1; }
  local tools="$tree/scripts/vm-tools" rev snap
  [[ -x "$tools/update-in-place" ]] || { echo "$tools/update-in-place is missing: this checkout has no vm-tools." >&2; exit 1; }
  rev=$(git -C "$tree" rev-parse --short HEAD)
  stop
  snap="before-refresh-$(date +%Y%m%d-%H%M%S)-$rev"
  qemu-img snapshot -c "$snap" "$disk"
  echo "Kept a restore point of the disk: $snap"
  echo "  put it back, with the VM off:  qemu-img snapshot -a $snap $disk     (list: qemu-img snapshot -l $disk)"
  mkdir -p "$vm"; wslg_check
  (setsid "$tree/scripts/run-vm.sh" --installed >"$vm/qemu.log" 2>&1 < /dev/null &)
  for _ in $(seq 60); do [[ -S "$vm/serial.sock" ]] && break; sleep 1; done
  (setsid python3 "$tools/serialpump" >"$vm/pump.log" 2>&1 < /dev/null &)
  sleep 25   # until the guest shows its login prompt on the serial console
  "$tools/vmlogin"
  "$tools/update-in-place" --reboot HEAD
  { iso_key; git -C "$tree" log -1 --format='%h %s (refreshed in place)'; } > "$out/.installed"
  echo "Refreshed: the VM runs $(git -C "$tree" log -1 --format='%h %s')."
}

if [[ "${1:-}" == stop ]]; then stop; exit 0; fi
[[ " $* " == *" --yes "* ]] && export BOMBADIL_YES=1
setup
sync_tree
case "${1:-}" in
  "") if [[ -f "$out/.installed" && -f "$disk" ]]; then
        echo "Booting the installed VM: $(sed -n 2p "$out/.installed")"
        [[ "$(head -1 "$out/.installed")" == "$(iso_key)" ]] ||
          echo "This checkout has a newer Bombadil; 'bombadil-vm refresh' puts it on the VM and keeps its disk ('reinstall' wipes it)."
      else install; fi
      run --installed ;;
  live) build; run ;;
  reinstall) install; run --installed ;;
  refresh) refresh ;;
  build) build ;;
  stop) stop ;;
  *) sed -n '2,18p' "$0"; exit 2 ;;
esac

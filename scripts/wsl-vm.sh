#!/usr/bin/env bash
# Build, install and boot Bombadil in a VM window from WSL. scripts\bombadil-vm.cmd runs this as
# root in a WSL distro named "bombadil"; it also works on any Arch host. It builds the commit this
# checkout has checked out, in a clone on a Linux filesystem (a Windows checkout loses symlinks and
# exec bits), and the VM's disk lives in that clone's out/.
#   wsl-vm.sh            boot the installed disk; the first time, build the ISO and install it
#   wsl-vm.sh live       boot the live ISO (the disk is not touched)
#   wsl-vm.sh reinstall  rebuild the ISO if the checkout changed it, then wipe the disk and install
#   wsl-vm.sh build      only build the ISO
# MEM (default 5G), SMP (4) and GL (0) go to run-vm.sh. WSLg's host OpenGL is often llvmpipe, and
# then the guest rendering in software itself is the faster of the two.
set -euo pipefail
[[ $EUID == 0 ]] || { echo "run as root (wsl -d bombadil -u root ...)"; exit 2; }
src="$(cd "$(dirname "$0")/.." && pwd)"
tree="${BOMBADIL_TREE:-/root/Bombadil}"
out="$tree/out"; vm="$out/vm"; disk="$out/bombadil.qcow2"
export MEM="${MEM:-5G}" SMP="${SMP:-4}" GL="${GL:-0}"

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
iso_key() { git -C "$tree" rev-parse HEAD:bin HEAD:src HEAD:shell HEAD:share HEAD:iso HEAD:scripts/build-iso.sh | sha1sum | cut -c1-12; }

build() {
  local key; key=$(iso_key)
  if [[ -f "$out/.iso-key" && "$(cat "$out/.iso-key")" == "$key" ]] && ls "$out"/*.iso >/dev/null 2>&1; then return; fi
  echo "Building the ISO for $(git -C "$tree" log -1 --format='%h %s') (15 to 30 minutes)..."
  rm -f "$out"/*.iso "$out/.iso-key"
  (cd "$tree" && SOURCE_DATE_EPOCH=$(git log -1 --format=%ct) WORK=/var/tmp/bombadil-work scripts/build-iso.sh)
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

# Install onto a fresh disk without a window: boot the ISO's serial-console entry with the smoke
# test edited off its command line, which leaves a root shell on the serial port, and run
# bombadil-install there. The installed system keeps a serial console (a login for "user").
install() {
  build
  local iso; iso=$(ls -t "$out"/*.iso | head -1)
  local log="$vm/install.log" qmp="$vm/install.qmp" sock="$vm/install.sock"
  mkdir -p "$vm"; : > "$log"; rm -f "$qmp" "$sock" "$out/.installed" "$disk"
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
  # Typed a few bytes at a time and the socket held open: QEMU drops whatever the UART hasn't
  # taken yet when the client hangs up. The echo is split so the typed line never matches it.
  python3 - "$sock" <<'PY'
import socket, sys, time
s = socket.socket(socket.AF_UNIX); s.connect(sys.argv[1])
line = (b"BOMBADIL_INSTALL_CMDLINE='console=tty0 console=ttyS0,115200' bombadil-install /dev/vda --yes"
        b" && echo INSTALL-''DONE; poweroff\r")
for i in range(0, len(line), 8):
    s.sendall(line[i:i + 8]); time.sleep(0.05)
time.sleep(1)
PY
  wait $pid || true
  grep -aq "INSTALL-DONE" "$log" || { echo "install failed; see $log"; exit 1; }
  { iso_key; git -C "$tree" log -1 --format='%h %s'; } > "$out/.installed"
  echo "Installed Bombadil ($(sed -n 2p "$out/.installed")) onto $disk."
}

setup
sync_tree
case "${1:-}" in
  "") if [[ -f "$out/.installed" && -f "$disk" ]]; then
        echo "Booting the installed VM: $(sed -n 2p "$out/.installed")"
        [[ "$(head -1 "$out/.installed")" == "$(iso_key)" ]] ||
          echo "This checkout has a newer Bombadil; 'bombadil-vm reinstall' puts it on the VM (and wipes the VM's disk)."
      else install; fi
      exec "$tree/scripts/run-vm.sh" --installed ;;
  live) build; exec "$tree/scripts/run-vm.sh" ;;
  reinstall) install; exec "$tree/scripts/run-vm.sh" --installed ;;
  build) build ;;
  *) sed -n '2,12p' "$0"; exit 2 ;;
esac

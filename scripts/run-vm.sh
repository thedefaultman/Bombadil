#!/usr/bin/env bash
# Boot Bombadil in a QEMU window.
#   scripts/run-vm.sh              live boot of the newest ISO
#   scripts/run-vm.sh --disk       live boot with a 40G disk to install onto (created if missing)
#   scripts/run-vm.sh --installed  boot the installed disk, no ISO
# MEM and SMP size the VM; GL=0 swaps virtio-vga-gl for plain virtio-vga (the guest renders in
# software), for hosts without working host OpenGL. While it runs, out/vm/qmp.sock takes
# `scripts/qmp.py out/vm/qmp.sock screenshot shot.png`, out/vm/serial.log has the serial console
# and host port 2222 reaches the guest's ssh.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
vm="$root/out/vm"; mkdir -p "$vm"
disk="$root/out/bombadil.qcow2"
ovmf=""
for f in /usr/share/edk2/x64/OVMF.4m.fd /usr/share/OVMF/OVMF_CODE_4M.fd /usr/share/OVMF/OVMF_CODE.fd; do
  [[ -f "$f" ]] && { ovmf="$f"; break; }
done
[[ "$ovmf" ]] || { echo "no OVMF firmware found; install edk2-ovmf (Arch) or ovmf (Debian/Ubuntu)"; exit 2; }
gpu=(-device virtio-vga-gl -display gtk,gl=on)
[[ "${GL:-1}" == 0 ]] && gpu=(-device virtio-vga -display gtk)
args=(-enable-kvm -m "${MEM:-6G}" -smp "${SMP:-4}" -cpu host -name Bombadil "${gpu[@]}"
      -device virtio-keyboard -device virtio-tablet -device intel-hda -device hda-duplex
      -netdev "user,id=n0,hostfwd=tcp:127.0.0.1:${SSH_PORT:-2222}-:22" -device virtio-net,netdev=n0
      -bios "$ovmf" -qmp "unix:$vm/qmp.sock,server,nowait"
      -chardev "socket,id=s0,path=$vm/serial.sock,server=on,wait=off,logfile=$vm/serial.log" -serial chardev:s0)
newest_iso() { ls -t "$root"/out/*.iso 2>/dev/null | head -1 | grep . || { echo "no ISO in $root/out; run scripts/build-iso.sh" >&2; exit 2; }; }
case "${1:-}" in
  "") args+=(-cdrom "$(newest_iso)" -boot d) ;;
  --disk)
    [[ -f "$disk" ]] || qemu-img create -q -f qcow2 "$disk" 40G
    args+=(-cdrom "$(newest_iso)" -boot d -drive file="$disk",if=virtio,format=qcow2) ;;
  --installed)
    [[ -f "$disk" ]] || { echo "no disk at $disk; install first with --disk"; exit 2; }
    args+=(-drive file="$disk",if=virtio,format=qcow2) ;;
  *) sed -n '2,9p' "$0"; exit 2 ;;
esac
rm -f "$vm/qmp.sock" "$vm/serial.sock"
exec qemu-system-x86_64 "${args[@]}" "${@:2}"

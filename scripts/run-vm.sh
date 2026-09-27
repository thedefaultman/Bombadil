#!/usr/bin/env bash
# Boot the newest ISO in QEMU with a virtio GPU so Hyprland gets acceleration.
#   scripts/run-vm.sh            live boot
#   scripts/run-vm.sh --disk     also attach a 40G disk to install onto (created if missing)
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
iso="$(ls -t "$root"/out/*.iso | head -1)"
args=(-enable-kvm -m 6G -smp 4 -cpu host
      -device virtio-vga-gl -display gtk,gl=on
      -device virtio-keyboard -device virtio-mouse -device intel-hda -device hda-duplex
      -netdev user,id=n0 -device virtio-net,netdev=n0
      -bios /usr/share/edk2/x64/OVMF.4m.fd
      -cdrom "$iso" -boot d)
if [[ "${1:-}" == "--disk" ]]; then
  disk="$root/out/bombadil.qcow2"
  [[ -f "$disk" ]] || qemu-img create -f qcow2 "$disk" 40G
  args+=(-drive file="$disk",if=virtio,format=qcow2)
fi
exec qemu-system-x86_64 "${args[@]}"

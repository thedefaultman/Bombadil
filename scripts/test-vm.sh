#!/usr/bin/env bash
# Boot the newest ISO headless with the serial/smoke entry, capture the serial log and
# a screenshot, and exit 0 when the smoke test reports no failures.
#
#   scripts/test-vm.sh                # uses KVM if /dev/kvm exists, else software emulation (slow)
#   TIMEOUT=1800 scripts/test-vm.sh
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
iso="${ISO:-$(ls -t "$root"/out/*.iso | head -1)}"
out="$root/out/test"; mkdir -p "$out"
log="$out/serial.log"; : > "$log"
qmp="$out/qmp.sock"; rm -f "$qmp"
timeout="${TIMEOUT:-1500}"

ovmf=""
for f in /usr/share/edk2/x64/OVMF_CODE.4m.fd /usr/share/edk2/x64/OVMF.4m.fd /usr/share/OVMF/OVMF_CODE_4M.fd /usr/share/OVMF/OVMF_CODE.fd; do
  [[ -f "$f" ]] && { ovmf="$f"; break; }
done
[[ "$ovmf" ]] || { echo "no OVMF firmware found; install edk2-ovmf (Arch) or ovmf (Debian/Ubuntu)"; exit 2; }

accel=(-accel tcg,thread=multi -cpu max)
[[ -w /dev/kvm ]] && accel=(-enable-kvm -cpu host)

qemu-system-x86_64 "${accel[@]}" -m 4G -smp "$(nproc)" \
  -drive if=pflash,format=raw,readonly=on,file="$ovmf" \
  -device virtio-vga -display none -vnc "${VNC:-127.0.0.1:99}" \
  -device virtio-keyboard -device virtio-mouse \
  -netdev user,id=n0 -device virtio-net,netdev=n0 \
  -serial "file:$log" -qmp "unix:$qmp,server,nowait" \
  -cdrom "$iso" -boot d -no-reboot \
  &
pid=$!
trap 'kill $pid 2>/dev/null || true' EXIT

# The smoke entry is second in the systemd-boot menu: once the menu is up, press Down+Enter.
sleep "${MENU_WAIT:-25}"
python3 "$root/scripts/qmp.py" "$qmp" send-keys down ret || true

start=$(date +%s)
while kill -0 $pid 2>/dev/null; do
  if grep -q "BOMBADIL-SMOKE: DONE" "$log"; then break; fi
  if (( $(date +%s) - start > timeout )); then echo "timed out after ${timeout}s"; break; fi
  sleep 5
done
python3 "$root/scripts/qmp.py" "$qmp" screenshot "$out/screen.png" || true
wait $pid || true
echo "--- smoke results ---"
grep "BOMBADIL-SMOKE" "$log" || { echo "no smoke output; see $log"; exit 1; }
grep -q "BOMBADIL-SMOKE: DONE pass=[0-9]* fail=0" "$log"

#!/usr/bin/env bash
# Boot the newest ISO headless with a smoke entry, capture the serial log and screenshots,
# and exit 0 when the smoke test reports no failures.
#
#   scripts/test-vm.sh                # live checks: session, bar, browser panel, a native app, sign-in
#   MODE=install scripts/test-vm.sh   # live checks, install to a scratch disk, boot it, test undo
#   TIMEOUT=2400 scripts/test-vm.sh   # uses KVM if /dev/kvm exists, else software emulation (slow)
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
iso="${ISO:-$(ls -t "$root"/out/*.iso | head -1)}"
mode="${MODE:-live}"
out="$root/out/test"; mkdir -p "$out"
timeout="${TIMEOUT:-2400}"

ovmf=""
for f in /usr/share/edk2/x64/OVMF_CODE.4m.fd /usr/share/edk2/x64/OVMF.4m.fd /usr/share/OVMF/OVMF_CODE_4M.fd /usr/share/OVMF/OVMF_CODE.fd; do
  [[ -f "$f" ]] && { ovmf="$f"; break; }
done
[[ "$ovmf" ]] || { echo "no OVMF firmware found; install edk2-ovmf (Arch) or ovmf (Debian/Ubuntu)"; exit 2; }

# Without KVM, Mesa's llvmpipe JIT crashes on QEMU's emulated AVX2 (seen in Hyprland and
# Quickshell's render threads), so emulate a CPU without AVX.
accel=(-accel tcg,thread=multi -cpu "${QEMU_CPU:-Nehalem}")
[[ -w /dev/kvm ]] && accel=(-enable-kvm -cpu host)
disk="$out/disk.qcow2"
if [[ "$mode" == "install" ]]; then rm -f "$disk"; qemu-img create -q -f qcow2 "$disk" 40G; fi

# boot NAME DONE_PATTERN [qemu args...]: run one VM until the pattern shows up in its serial log.
boot() {
  local name=$1 done=$2; shift 2
  local log="$out/$name.serial.log" qmp="$out/$name.qmp.sock"
  : > "$log"; rm -f "$qmp" "$out/$name".keys.* "$out/$name"-*.png "$out/$name"-*.png.ppm
  qemu-system-x86_64 "${accel[@]}" -m 4G -smp "$(nproc)" \
    -drive if=pflash,format=raw,readonly=on,file="$ovmf" \
    -device virtio-vga -display none -vnc "${VNC:-127.0.0.1:99}" \
    -device virtio-keyboard -device virtio-mouse \
    -audiodev none,id=snd0 -device intel-hda -device hda-duplex,audiodev=snd0 \
    -netdev user,id=n0 -device virtio-net,netdev=n0 \
    -chardev "socket,id=s0,path=$out/$name.serial.sock,server=on,wait=off,logfile=$log" -serial chardev:s0 -qmp "unix:$qmp,server,nowait" "$@" &
  local pid=$!
  trap "kill $pid 2>/dev/null || true" EXIT
  if [[ "${MENU_DOWN:-}" ]]; then
    # Pick a smoke entry from the systemd-boot menu as soon as it shows on the serial console
    # (it counts down from a few seconds).
    for _ in $(seq 240); do grep -aq "Boot in" "$log" && break; sleep 0.5; done
    local keys=(); for _ in $(seq "$MENU_DOWN"); do keys+=(down); done
    python3 "$root/scripts/qmp.py" "$qmp" send-keys "${keys[@]}" ret || true
  fi
  local start; start=$(date +%s)
  while kill -0 $pid 2>/dev/null; do
    grep -aqE "$done" "$log" && break
    # The smoke test asks for screenshots at interesting moments: "BOMBADIL-SMOKE: SHOT <name>".
    for shot in $(grep -ao "BOMBADIL-SMOKE: SHOT [a-z0-9-]*" "$log" | awk '{print $3}'); do
      [[ -f "$out/$name-$shot.png" ]] || python3 "$root/scripts/qmp.py" "$qmp" screenshot "$out/$name-$shot.png" || true
    done
    # ...and key presses: "BOMBADIL-SMOKE: KEYS <n> <qcode>..." (a Super tap, a word), each once.
    while read -r n keys; do
      [[ -f "$out/$name.keys.$n" ]] && continue
      touch "$out/$name.keys.$n"
      # shellcheck disable=SC2086
      python3 "$root/scripts/qmp.py" "$qmp" send-keys $keys || true
    done < <(grep -ao "BOMBADIL-SMOKE: KEYS [0-9]* [a-z0-9_+ ]*" "$log" | cut -d' ' -f3-)
    if (( $(date +%s) - start > timeout )); then echo "$name: timed out after ${timeout}s"; break; fi
    sleep 2
  done
  python3 "$root/scripts/qmp.py" "$qmp" screenshot "$out/$name-last.png" 2>/dev/null || true
  wait $pid || true
  trap - EXIT
  echo "--- $name ---"
  grep -a "BOMBADIL-SMOKE" "$log" || echo "no smoke output; see $log"
}

if [[ "$mode" == "install" ]]; then
  MENU_DOWN=2 boot live-install "BOMBADIL-SMOKE: DONE" -cdrom "$iso" -drive file="$disk",if=virtio,format=qcow2 -boot d -no-reboot
  grep -aq "BOMBADIL-SMOKE: PASS install" "$out/live-install.serial.log" || exit 1
  # The installed system reboots itself once (the undo applies on boot), so no -no-reboot here.
  MENU_DOWN="" boot installed "BOMBADIL-SMOKE: DONE" -drive file="$disk",if=virtio,format=qcow2
  logs=("$out/live-install.serial.log" "$out/installed.serial.log")
else
  MENU_DOWN=1 boot live "BOMBADIL-SMOKE: DONE" -cdrom "$iso" -boot d -no-reboot
  logs=("$out/live.serial.log")
fi
for l in "${logs[@]}"; do grep -aqE "BOMBADIL-SMOKE: DONE pass=[0-9]+ fail=0" "$l" || exit 1; done
! grep -aq "BOMBADIL-SMOKE: FAIL" "${logs[@]}"

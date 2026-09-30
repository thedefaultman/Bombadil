#!/usr/bin/env bash
# Boot the newest ISO headless with a smoke entry, capture the serial log and screenshots,
# and exit 0 when the smoke test reports no failures.
#
#   scripts/test-vm.sh                # live checks: session, bar, browser panel, a native app, sign-in
#   MODE=install scripts/test-vm.sh   # live checks, install to a scratch disk, boot it, test the layout and undo
#   MODE=install-encrypted scripts/test-vm.sh
#                                     # the installer with a password: GRUB is asked for it once, nothing asks again
#   MODE=refresh OLD_ISO=old.iso scripts/test-vm.sh
#                                     # install with OLD_ISO, refresh the disk with this ISO, boot it, put the old system back
#   TIMEOUT=2400 scripts/test-vm.sh   # uses KVM if /dev/kvm exists, else software emulation (slow)
#
# The install modes need an ISO built with BOMBADIL_TEST_ENTRIES=1 (scripts/build-in-container.sh), which has
# the boot entries that run them. The ISO is attached as a USB stick with 8 GB of memory, which is how the
# installer meets copy-to-RAM on a real stick.
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
case "$mode" in
  live) ;;
  install|install-encrypted|refresh) rm -f "$disk"; qemu-img create -q -f qcow2 "$disk" 40G ;;
  *) echo "unknown MODE=$mode"; exit 2 ;;
esac

# The firmware keeps its variables (the boot entry GRUB makes) in a file of its own, where the machine
# has a split firmware image; one disk, one set of variables.
vars=""
for f in "${ovmf/OVMF_CODE/OVMF_VARS}" "${ovmf/OVMF.4m/OVMF_VARS.4m}"; do
  [[ "$f" != "$ovmf" && -f "$f" ]] && { vars="$f"; break; }
done
firmware=(-drive if=pflash,format=raw,readonly=on,file="$ovmf")
if [[ "$vars" ]]; then
  cp "$vars" "$out/OVMF_VARS.fd"
  firmware+=(-drive if=pflash,format=raw,file="$out/OVMF_VARS.fd")
fi

# The installer meets the USB stick it runs from the way a person does: the ISO is a USB disk, and with 8 GB
# of memory the image is copied into RAM and the stick is no longer mounted.
ram=4G
[[ "$mode" == live ]] || ram=8G
stick_for() {  # stick_for ISO: sets STICK, the QEMU arguments that attach ISO as a USB disk
  STICK=(-device qemu-xhci,id=xhci -drive "if=none,id=stick,format=raw,readonly=on,file=$1"
         -device usb-storage,bus=xhci.0,drive=stick,bootindex=0)
}

# OVMF does not see a virtio keyboard, so GRUB's password prompt is typed on a USB one; the live session
# has the virtio one.
KEYBOARD=(-device virtio-keyboard)

# finished LOG PATTERN: the pattern is an extended regular expression, or with PCRE=1 a Perl one that can
# span lines (for "this, and later that").
finished() { if [[ "${PCRE:-}" ]]; then grep -aPzq "$2" "$1"; else grep -aqE "$2" "$1"; fi; }

# type_keys TEXT: the QMP key names that type TEXT (lower case, digits, dash), then Enter.
type_keys() {
  local t=$1 i c
  for ((i = 0; i < ${#t}; i++)); do
    c=${t:i:1}
    case "$c" in -) echo -n "minus " ;; ' ') echo -n "spc " ;; *) echo -n "$c " ;; esac
  done
  echo ret
}

# boot NAME DONE_PATTERN [qemu args...]: run one VM until the pattern shows up in its serial log.
#   TYPE_WHEN=PATTERN TYPE_TEXT=TEXT  types TEXT once PATTERN is on the serial log (GRUB's password prompt)
#   STOP=1                            ends the VM at the pattern instead of waiting for it to power off
boot() {
  local name=$1 done=$2; shift 2
  local log="$out/$name.serial.log" qmp="$out/$name.qmp.sock"
  : > "$log"; rm -f "$qmp" "$out/$name".keys.* "$out/$name".typed "$out/$name"-*.png "$out/$name"-*.png.ppm
  qemu-system-x86_64 "${accel[@]}" -m "${RAM:-$ram}" -smp "$(nproc)" \
    "${firmware[@]}" \
    -device virtio-vga -display none -vnc "${VNC:-127.0.0.1:99}" \
    "${KEYBOARD[@]}" -device virtio-mouse \
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
    finished "$log" "$done" && break
    if [[ "${TYPE_WHEN:-}" && ! -f "$out/$name.typed" ]] && grep -aq "$TYPE_WHEN" "$log"; then
      touch "$out/$name.typed"
      sleep 3  # the prompt is ready a moment after it is printed
      # shellcheck disable=SC2046
      python3 "$root/scripts/qmp.py" "$qmp" send-keys $(type_keys "$TYPE_TEXT") || true
    fi
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
  [[ -z "${STOP:-}" ]] || kill $pid 2>/dev/null || true
  wait $pid || true
  trap - EXIT
  echo "--- $name ---"
  grep -a "BOMBADIL-SMOKE" "$log" || echo "no smoke output; see $log"
}

# Every line the installer and the installed system print is on a serial log; the tests read them there.
need_entries() {  # need_entries ISO TEXT: the ISO has the boot entry whose options hold TEXT
  grep -aqF -- "$2" "$1" || { echo "$1 has no boot entry for $2: build it with BOMBADIL_TEST_ENTRIES=1 scripts/build-in-container.sh"; exit 2; }
}

case "$mode" in
  install)
    need_entries "$iso" "bombadil.smoke=install"
    stick_for "$iso"
    MENU_DOWN=2 boot live-install "BOMBADIL-SMOKE: DONE" "${STICK[@]}" -drive file="$disk",if=virtio,format=qcow2 -boot menu=off -no-reboot
    grep -aq "BOMBADIL-SMOKE: PASS install$" "$out/live-install.serial.log" || exit 1
    # The installed system reboots itself once (the undo applies on boot), so no -no-reboot here.
    MENU_DOWN="" boot installed "BOMBADIL-SMOKE: DONE" -drive file="$disk",if=virtio,format=qcow2
    logs=("$out/live-install.serial.log" "$out/installed.serial.log")
    ;;
  install-encrypted)
    need_entries "$iso" "bombadil.smoke=install-encrypted"
    stick_for "$iso"
    MENU_DOWN=3 boot live-install "BOMBADIL-SMOKE: DONE" "${STICK[@]}" -drive file="$disk",if=virtio,format=qcow2 -boot menu=off -no-reboot
    grep -aq "BOMBADIL-SMOKE: PASS install-encrypted$" "$out/live-install.serial.log" || exit 1
    # GRUB asks for the password; typed there, nothing after it may ask again (no key is typed to the initramfs).
    KEYBOARD=(-device qemu-xhci -device usb-kbd)
    TYPE_WHEN="Enter passphrase" TYPE_TEXT="correct-horse-battery-staple" \
      MENU_DOWN="" boot installed "BOMBADIL-SMOKE: DONE" -drive file="$disk",if=virtio,format=qcow2
    grep -aq "Enter passphrase" "$out/installed.serial.log" || { echo "GRUB never asked for the password"; exit 1; }
    ! grep -aqiE "Please enter passphrase|Enter passphrase for /dev" "$out/installed.serial.log" || { echo "a second password prompt"; exit 1; }
    logs=("$out/live-install.serial.log" "$out/installed.serial.log")
    ;;
  refresh)
    old_iso="${OLD_ISO:?MODE=refresh needs OLD_ISO: an ISO built before this disk layout, with its install test entry}"
    need_entries "$old_iso" "bombadil.smoke=install"
    need_entries "$iso" "bombadil.smoke=refresh"
    # 1. The disk as an older Bombadil installed it.
    stick_for "$old_iso"
    MENU_DOWN=2 boot old-install "BOMBADIL-SMOKE: DONE" "${STICK[@]}" -drive file="$disk",if=virtio,format=qcow2 -boot menu=off -no-reboot
    grep -aq "BOMBADIL-SMOKE: PASS install$" "$out/old-install.serial.log" || { echo "the old ISO did not install"; exit 1; }
    # 2. This ISO refreshes it, keeping the home folder.
    stick_for "$iso"
    MENU_DOWN=4 boot refresh "BOMBADIL-SMOKE: DONE" "${STICK[@]}" -drive file="$disk",if=virtio,format=qcow2 -boot menu=off -no-reboot
    grep -aq "BOMBADIL-SMOKE: PASS refresh-runs$" "$out/refresh.serial.log" || exit 1
    # 3. The refreshed system starts, and puts the old system back: it then starts from the same GRUB.
    MENU_DOWN="" STOP=1 PCRE=1 boot refreshed "(?s)BOMBADIL-SMOKE: REBOOT.*BOMBADIL-SMOKE: PASS hyprland-running" \
      -drive file="$disk",if=virtio,format=qcow2
    logs=("$out/old-install.serial.log" "$out/refresh.serial.log" "$out/refreshed.serial.log")
    ;;
  live)
    MENU_DOWN=1 boot live "BOMBADIL-SMOKE: DONE" -cdrom "$iso" -boot d -no-reboot
    logs=("$out/live.serial.log")
    ;;
esac
if [[ "$mode" == refresh ]]; then
  # The refreshed system's own checks end at the reboot; the old system then starts and its session comes up.
  grep -aqE "BOMBADIL-SMOKE: REBOOT pass=[0-9]+ fail=0" "$out/refreshed.serial.log" || exit 1
  for l in "$out/old-install.serial.log" "$out/refresh.serial.log"; do grep -aqE "BOMBADIL-SMOKE: DONE pass=[0-9]+ fail=0" "$l" || exit 1; done
  ! grep -aq "BOMBADIL-SMOKE: FAIL" "$out/refresh.serial.log" "$out/refreshed.serial.log" "$out/old-install.serial.log"
  exit
fi
for l in "${logs[@]}"; do grep -aqE "BOMBADIL-SMOKE: DONE pass=[0-9]+ fail=0" "$l" || exit 1; done
! grep -aq "BOMBADIL-SMOKE: FAIL" "${logs[@]}"

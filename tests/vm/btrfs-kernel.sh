#!/usr/bin/env bash
# Boot Arch's own `linux` kernel under QEMU on a btrfs disk laid out the way bombadil-install
# lays it out (@ on /, @home on /home, @snapshots on /.snapshots, plus a nested subvolume
# @home/user/Projects), run TEST_DIR/run.sh as root inside it, print what it printed and exit
# with its status. For checking kernel behaviour (fanotify, subvolumes...) on the real layout.
#
#   tests/vm/btrfs-kernel.sh TEST_DIR [-- extra kernel args]
#   TIMEOUT=900       seconds before QEMU is killed
#   REBUILD=1         rebuild the cached rootfs/kernel (e.g. to pick up a newer kernel)
#   VERBOSE=1         stream the whole serial console, not just the test's output
#   DISK_SIZE=8G      size of the (sparse) disk image
#
# Needs docker (or podman, CONTAINER_RUNTIME=...) and qemu-system-x86_64; uses KVM when there
# is one, else software emulation. The first run builds an Arch rootfs, kernel and initramfs into
# out/btrfs-vm/ (network needed; behind a proxy set BOMBADIL_HOST_NET=1 as for
# scripts/build-in-container.sh). Later runs only make a fresh disk image and boot it, offline.
# There is no systemd in the VM: the kernel's init is btrfs-init.sh, which runs the test.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
test_dir="$(cd "${1:?usage: tests/vm/btrfs-kernel.sh TEST_DIR [-- extra kernel args]}" && pwd)"
shift; [[ "${1:-}" == "--" ]] && shift
[[ -f "$test_dir/run.sh" ]] || { echo "btrfs-kernel: no run.sh in $test_dir" >&2; exit 2; }
runtime="${CONTAINER_RUNTIME:-$(command -v docker || command -v podman)}"
image="${BOMBADIL_BUILD_IMAGE:-archlinux:base}"
cache="$root/out/btrfs-vm"
packages="base linux mkinitcpio btrfs-progs python util-linux procps-ng strace inotify-tools"
log() { echo "btrfs-kernel: $*" >&2; }

# --- Once: the rootfs (as a tarball), Arch's kernel and an initramfs, cached in out/btrfs-vm/.
if [[ "${REBUILD:-}" || ! -f "$cache/rootfs.tar.zst" || "$(cat "$cache/packages" 2>/dev/null)" != "$packages" ]]; then
  log "building the Arch rootfs, kernel and initramfs into $cache (once)"
  start=$SECONDS; mkdir -p "$cache"; rm -f "$cache/packages"
  args=(--rm --privileged -v "$cache:/out" -e PACKAGES="$packages")
  [[ "${BOMBADIL_HOST_NET:-}" ]] && args+=(--network host)
  for v in HTTP_PROXY HTTPS_PROXY http_proxy https_proxy NO_PROXY no_proxy; do
    [[ "${!v:-}" ]] && args+=(-e "$v=${!v}")
  done
  if [[ "${SSL_CERT_FILE:-}" ]]; then
    args+=(-v "$SSL_CERT_FILE:/etc/bombadil-ca.crt:ro" -e SSL_CERT_FILE=/etc/bombadil-ca.crt -e CURL_CA_BUNDLE=/etc/bombadil-ca.crt)
  fi
  "$runtime" run "${args[@]}" "$image" bash -euo pipefail -c '
    pacman -Syu --noconfirm --needed arch-install-scripts >/dev/null
    r=/tmp/rootfs; mkdir -p $r/etc/mkinitcpio.d
    # A preset of our own, made before the linux package is installed, so its hook builds an
    # initramfs that mounts a btrfs root on virtio-blk. (The stock preset would autodetect the
    # modules of whatever machine runs this container.)
    printf "%s\n" "MODULES=(virtio_pci virtio_blk btrfs)" "HOOKS=(base)" > $r/etc/mkinitcpio-btrfs-vm.conf
    printf "%s\n" "PRESETS=(default)" "ALL_kver=/boot/vmlinuz-linux" \
      "default_config=/etc/mkinitcpio-btrfs-vm.conf" "default_image=/boot/initramfs-linux.img" \
      > $r/etc/mkinitcpio.d/linux.preset
    pacstrap -c $r $PACKAGES >/dev/null
    # What bombadil-install sets up: the user (uid 1000, group user) and the hostname.
    useradd --root $r -m -G wheel,video,input,audio user
    echo bombadil > $r/etc/hostname
    pacman --root $r -Q > /out/package-versions
    basename "$(dirname $r/usr/lib/modules/*/vmlinuz)" > /out/kernel-release
    install -m 644 $r/boot/vmlinuz-linux $r/boot/initramfs-linux.img /out/
    tar -C $r --numeric-owner --xattrs --xattrs-include="*" --zstd -cf /out/rootfs.tar.zst .
  '
  echo "$packages" > "$cache/packages"
  log "built $(cat "$cache/kernel-release") rootfs in $((SECONDS - start))s"
fi

# --- Every run: a fresh disk image from the cached rootfs plus the test, then boot it.
run="$cache/runs/$(date +%Y%m%d-%H%M%S)-$$"; mkdir -p "$run"
trap 'rm -f "$run/disk.img"' EXIT
start=$SECONDS
"$runtime" run --rm --network none --tmpfs /stage:exec -v "$cache:/cache:ro" -v "$run:/out" -v "$test_dir:/test-src:ro" \
  -v "$here/btrfs-init.sh:/btrfs-init.sh:ro" -e DISK_SIZE="${DISK_SIZE:-8G}" -e OWNER="$(id -u):$(id -g)" \
  "$image" bash -euo pipefail -c '
  s=/stage; mkdir $s/root
  tar -C $s/root --numeric-owner --xattrs --xattrs-include="*" --zstd -xpf /cache/rootfs.tar.zst
  # As bombadil-install does it: /home lives in @home and @snapshots is mounted on
  # /.snapshots. mkfs numbers subvolumes depth first in readdir order (newest first on tmpfs),
  # so this order gives @ 256 and @home 257 as on an installed system (@snapshots gets 259).
  mkdir -m 750 $s/@snapshots; mv $s/root/home $s/@home; mv $s/root $s/@
  mkdir $s/@/home $s/@/.snapshots
  # The dev brief adds ~/Projects as a nested subvolume inside @home.
  mkdir $s/@home/user/Projects; chown --reference=$s/@home/user $s/@home/user/Projects
  printf "%s\n" \
    "LABEL=bombadil  /            btrfs  rw,relatime,compress=zstd:3,space_cache=v2,subvol=/@      0 0" \
    "LABEL=bombadil  /home        btrfs  rw,relatime,compress=zstd:3,space_cache=v2,subvol=/@home  0 0" \
    "LABEL=bombadil  /.snapshots  btrfs  subvol=/@snapshots  0 0" > $s/@/etc/fstab
  cp -rT /test-src $s/@/test; chmod +x $s/@/test/run.sh
  install -m 755 /btrfs-init.sh $s/@/usr/bin/bombadil-test-init
  # mkfs.btrfs needs --subvol (btrfs-progs >= 6.10), so run the one in the rootfs, via its
  # own loader and libraries. --compress stands in for the installer copying under compress=zstd.
  truncate -s "$DISK_SIZE" /out/disk.img
  $s/@/usr/lib/ld-linux-x86-64.so.2 --library-path $s/@/usr/lib $s/@/usr/bin/mkfs.btrfs -q \
    -L bombadil --compress zstd -r $s -u @ -u @home -u @home/user/Projects -u @snapshots /out/disk.img
  chown "$OWNER" /out/disk.img
'
log "disk image ready in $((SECONDS - start))s; booting $(cat "$cache/kernel-release")"

start=$SECONDS
accel=(-accel tcg,thread=multi -cpu "${QEMU_CPU:-Nehalem}")
[[ -w /dev/kvm ]] && accel=(-accel kvm -cpu host)
: > "$run/serial.log"
[[ "${VERBOSE:-}" ]] && tail -f "$run/serial.log" --pid=$$ >&2 &
qrc=0
timeout -k 10 "${TIMEOUT:-900}" qemu-system-x86_64 "${accel[@]}" -m 2G -smp "$(nproc)" \
  -nodefaults -display none -no-reboot -serial file:"$run/serial.log" \
  -kernel "$cache/vmlinuz-linux" -initrd "$cache/initramfs-linux.img" \
  -append "console=ttyS0 loglevel=5 panic=-1 root=/dev/vda rootfstype=btrfs rootflags=subvol=@,compress=zstd rw init=/usr/bin/bombadil-test-init $*" \
  -drive file="$run/disk.img",if=virtio,format=raw || qrc=$?
[[ "${VERBOSE:-}" ]] && sleep 1  # let tail catch up

# The init script prints the test's output between "BTRFS-VM: BEGIN" and "BTRFS-VM: END rc=N".
sed -i 's/\r$//' "$run/serial.log"
rc="$(sed -n 's/^BTRFS-VM: END rc=\([0-9]*\).*/\1/p' "$run/serial.log")"
if [[ -z "$rc" ]]; then
  tail -n 40 "$run/serial.log" >&2
  if (( qrc == 124 )); then log "timed out after ${TIMEOUT:-900}s"; else log "the VM stopped before the test finished"; fi
  log "full serial log: $run/serial.log"
  exit $(( qrc == 124 ? 124 : 125 ))
fi
grep -a -m1 '^BTRFS-VM: BEGIN' "$run/serial.log" | sed 's/^BTRFS-VM: BEGIN /btrfs-kernel: booted /' >&2
sed -n '/^BTRFS-VM: BEGIN/,/^BTRFS-VM: END rc=/{//!p}' "$run/serial.log"
log "rc=$rc after $((SECONDS - start))s in the VM; serial log: $run/serial.log"
exit "$rc"

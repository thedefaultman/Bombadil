#!/usr/bin/env bash
# Build the Bombadil ISO. Needs an Arch host (or container) with archiso, fakeroot and binutils installed:
#   sudo pacman -S archiso fakeroot binutils   or   podman run --privileged -v "$PWD":/src archlinux ...
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
work="${WORK:-/tmp/bombadil-work}"
out="${OUT:-$root/out}"
profile="$work/profile"
# One build time for everything: the image, the package and the VERSION file inside it.
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(date +%s)}"
rm -rf "$profile"; mkdir -p "$profile" "$out"
cp -a "$root/iso/." "$profile/"
# The test entries of the boot menu (a serial console with the smoke test, and one that installs to
# /dev/vda without asking) are only in builds made for the VM tests, never in a release.
if [[ -z "${BOMBADIL_TEST_ENTRIES:-}" ]]; then
  rm -f "$profile"/efiboot/loader/entries/0[2-9]-*.conf
  # A BOMBADIL_DEV_SERVER left in the shell (it is meant for a package build for a test VM) must not put the VM's host
  # into the package of a release.
  if [[ -n "${BOMBADIL_DEV_SERVER:-}" ]]; then
    echo "Note: BOMBADIL_DEV_SERVER is ignored, because this is a release build (BOMBADIL_TEST_ENTRIES is not set)" >&2
  fi
  unset BOMBADIL_DEV_SERVER
else
  # The same builds are the ones a test VM installs from and updates from, so the package they carry also lists the
  # VM's host as a server (see stage_tree in scripts/build-package.sh). A release never does.
  export BOMBADIL_DEV_SERVER=1
fi
# Bombadil comes into the image as a package, like everything else on it: built into a repository here, read by
# pacstrap from a copy of iso/pacman.conf that names it, and left on the image for the installer and the first update.
# (BOMBADIL_COMMIT and BOMBADIL_PKGREL come from scripts/build-in-container.sh, which can read the checkout where the
# container cannot.)
rm -rf "$work/repo"
WORK="$work" "$root/scripts/build-package.sh" "$work/repo"
"$root/scripts/pacman-conf-with-repo.sh" "$root/iso/pacman.conf" "$profile/pacman.conf" "$work/repo"
grep -qx bombadil "$profile/packages.x86_64" || echo bombadil >> "$profile/packages.x86_64"
mkdir -p "$profile/airootfs/var/lib/bombadil"; chmod 755 "$profile/airootfs/var/lib/bombadil"
cp -a "$work/repo" "$profile/airootfs/var/lib/bombadil/repo"
# Bake the provider CLIs in, so the first-run picker only has to log in.
if [[ -z "${BOMBADIL_NO_CLIS:-}" ]]; then
  npm install -g --no-fund --no-audit --allow-scripts=@anthropic-ai/claude-code \
    --prefix "$profile/airootfs/usr" @anthropic-ai/claude-code @openai/codex
fi
# A work directory that is reused skips pacstrap, which would leave the last build's package in the image.
rm -rf "$work/build"
mkarchiso -v -w "$work/build" -o "$out" "$profile"

# What the package was meant to leave in the image is read back from the tree it was made from, before the
# image is read back: a unit that is not enabled or a command that is not a link is found here.
verify_package() {
  local rootfs="$work/build/x86_64/airootfs" kind unit n=0 found leak conf
  for kind in system user; do
    [[ -r "$rootfs/usr/share/bombadil/enable.$kind" ]] || { echo "Error: the image has no list of $kind units to enable" >&2; exit 1; }
    [[ -d "$rootfs/etc/systemd/$kind" ]] || { echo "Error: the image has no /etc/systemd/$kind, so it enables no $kind unit" >&2; exit 1; }
    while read -r unit || [[ -n "$unit" ]]; do
      [[ -z "$unit" || "$unit" == "#"* ]] && continue
      n=$((n + 1))
      # (A find that fails must not end the build without a word: set -e would, in an assignment.)
      found=$(find "$rootfs/etc/systemd/$kind" -path "*.wants/$unit" -type l -print -quit 2>/dev/null || true)
      [[ -n "$found" ]] || { echo "Error: the image does not enable $unit ($kind)" >&2; exit 1; }
    done < "$rootfs/usr/share/bombadil/enable.$kind"
  done
  (( n > 0 )) || { echo "Error: the package lists no units to enable" >&2; exit 1; }
  [[ -L "$rootfs/usr/bin/agentd" ]] || { echo "Error: /usr/bin/agentd is not a link into /usr/share/bombadil/bin" >&2; exit 1; }
  awk '/^\[bombadil\]/ { b = NR } /^\[core\]/ { c = NR } END { exit !(b && c && b < c) }' "$rootfs/etc/pacman.conf" \
    || { echo "Error: /etc/pacman.conf in the image does not name [bombadil] above [core]" >&2; exit 1; }
  # The laptop's host is a server of a build made for the VM tests and of no other: a release that lists it would
  # trust whatever answers at that address. What the image should hold is decided by the test entries of the boot menu
  # (BOMBADIL_TEST_ENTRIES), never by BOMBADIL_DEV_SERVER: that one is what a stray setting in the shell could change.
  conf="$rootfs/etc/pacman.d/bombadil.conf"
  [[ -r "$conf" ]] || { echo "Error: the image has no /etc/pacman.d/bombadil.conf" >&2; exit 1; }
  if grep -qF 10.0.2.2 "$conf"; then
    [[ -n "${BOMBADIL_TEST_ENTRIES:-}" ]] || { echo "Error: /etc/pacman.d/bombadil.conf in the image lists 10.0.2.2, and this is not a test build" >&2; exit 1; }
  elif [[ -n "${BOMBADIL_TEST_ENTRIES:-}" ]]; then
    echo "Error: /etc/pacman.d/bombadil.conf in the image does not list the host of the VM, and this is a test build" >&2; exit 1
  fi
  # The build-time repository is a path on this machine: it must not have leaked into anything the image keeps.
  leak=$(grep -rIlF -- "$work/repo" "$rootfs/etc" "$rootfs/var" "$rootfs/root" 2>/dev/null | head -n 1 || true)
  [[ -z "$leak" ]] || { echo "Error: ${leak#"$rootfs"} mentions the build directory $work/repo" >&2; exit 1; }
  echo "The image holds the package, its units, its commands and its repository entry."
}
verify_package

# The image is read back and compared with the tree it was made from: a file the image holds wrongly is
# found here, not as a driver that will not load on someone's laptop (see profiledef.sh).
verify_image() {
  local iso rootfs="$work/build/x86_64/airootfs" tmp differ
  iso=$(find "$out" -maxdepth 1 -name 'bombadil-*.iso' -printf '%T@ %p\n' | sort -n | tail -n 1 | cut -d' ' -f2-)
  if [[ $EUID -ne 0 ]] || ! command -v xorriso >/dev/null || [[ ! -d "$rootfs" ]]; then
    echo "Note: the image was not read back (needs root, xorriso and the tree it was made from)"; return 0
  fi
  tmp=$(mktemp -d); mkdir "$tmp/mnt"
  xorriso -osirrox on -indev "$iso" -extract /arch/x86_64/airootfs.erofs "$tmp/airootfs.erofs" >/dev/null 2>&1 \
    || { echo "Note: could not take the image out of $iso; not read back"; rm -rf "$tmp"; return 0; }
  if ! mount -t erofs -o ro,loop "$tmp/airootfs.erofs" "$tmp/mnt" 2>/dev/null; then
    echo "Note: this system cannot mount the image; it was not read back"; rm -rf "$tmp"; return 0
  fi
  differ=$(diff -rq "$rootfs" "$tmp/mnt" 2>&1 | grep ' differ$' || true)
  umount "$tmp/mnt"; rm -rf "$tmp"
  if [[ -n "$differ" ]]; then
    echo "Error: the image does not hold these files as they were:" >&2; head -n 20 <<<"$differ" >&2; exit 1
  fi
  echo "The image was read back and matches the tree it was made from."
}
verify_image
echo "ISO in $out"

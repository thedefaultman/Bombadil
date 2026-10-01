#!/usr/bin/env bash
# Build the Bombadil ISO. Needs an Arch host (or container) with archiso installed:
#   sudo pacman -S archiso   or   podman run --privileged -v "$PWD":/src archlinux ...
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
work="${WORK:-/tmp/bombadil-work}"
out="${OUT:-$root/out}"
profile="$work/profile"
rm -rf "$profile"; mkdir -p "$profile" "$out"
cp -a "$root/iso/." "$profile/"
# Ship the whole tree at /usr/share/bombadil so the live system and the installer both have it.
dest="$profile/airootfs/usr/share/bombadil"
mkdir -p "$dest"
cp -a "$root/bin" "$root/src" "$root/shell" "$root/share" "$root/install" "$root/iso/packages.x86_64" "$dest/"
# What this image is: the date and the commit. The installer records it, and names the first restore point after it.
echo "$(date --date="@${SOURCE_DATE_EPOCH:-$(date +%s)}" +%Y.%m.%d) ($(git -C "$root" rev-parse --short HEAD 2>/dev/null || echo unknown))" > "$dest/VERSION"
# The test entries of the boot menu (a serial console with the smoke test, and one that installs to
# /dev/vda without asking) are only in builds made for the VM tests, never in a release.
if [[ -z "${BOMBADIL_TEST_ENTRIES:-}" ]]; then
  rm -f "$profile"/efiboot/loader/entries/0[2-9]-*.conf
fi
mkdir -p "$profile/airootfs/usr/local/bin"
for b in agentd bombadil bombadil-app bombadil-browser bombadil-os-mcp bombadil-shell bombadil-brain bombadil-brain-watch; do
  ln -sfn "/usr/share/bombadil/bin/$b" "$profile/airootfs/usr/local/bin/$b"
done
# Bake the provider CLIs in, so the first-run picker only has to log in.
if [[ -z "${BOMBADIL_NO_CLIS:-}" ]]; then
  npm install -g --no-fund --no-audit --allow-scripts=@anthropic-ai/claude-code \
    --prefix "$profile/airootfs/usr" @anthropic-ai/claude-code @openai/codex
fi
mkarchiso -v -w "$work/build" -o "$out" "$profile"

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

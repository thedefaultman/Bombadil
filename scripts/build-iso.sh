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
cp -a "$root/bin" "$root/src" "$root/shell" "$root/share" "$root/iso/packages.x86_64" "$dest/"
mkdir -p "$profile/airootfs/usr/local/bin"
for b in agentd bombadil bombadil-app bombadil-browser bombadil-os-mcp bombadil-shell bombadil-mail bombadil-mail-host; do
  ln -sfn "/usr/share/bombadil/bin/$b" "$profile/airootfs/usr/local/bin/$b"
done
# Bake the provider CLIs in, so the first-run picker only has to log in.
if [[ -z "${BOMBADIL_NO_CLIS:-}" ]]; then
  npm install -g --no-fund --no-audit --allow-scripts=@anthropic-ai/claude-code \
    --prefix "$profile/airootfs/usr" @anthropic-ai/claude-code @openai/codex
fi
mkarchiso -v -w "$work/build" -o "$out" "$profile"
echo "ISO in $out"

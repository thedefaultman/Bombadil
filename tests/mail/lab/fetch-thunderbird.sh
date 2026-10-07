#!/usr/bin/env bash
# fetch-thunderbird.sh: download and unpack a Mozilla Thunderbird release build for the Mail lab.
#
#   tests/mail/lab/fetch-thunderbird.sh            -> prints the path of the thunderbird binary on stdout
#
# Idempotent: if ~/.cache/bombadil-lab/thunderbird-<ver>/thunderbird exists nothing is downloaded.
# Progress goes to stderr, so `TB=$(./fetch-thunderbird.sh)` works.
#
# Environment:
#   TB_VERSION   exact version, e.g. 157.0 (default), 156.0, or an ESR such as 140.4.0esr;
#                "latest" / "esr-latest" resolve through download.mozilla.org's redirect.
#   TB_LOCALE    default en-US
#   TB_URL       full tarball URL, overrides the computed one
#   BOMBADIL_LAB_CACHE   cache root (default ~/.cache/bombadil-lab); nothing is ever written in the repo.
set -euo pipefail

CACHE="${BOMBADIL_LAB_CACHE:-$HOME/.cache/bombadil-lab}"
TB_VERSION="${TB_VERSION:-157.0}"
TB_LOCALE="${TB_LOCALE:-en-US}"
BASE="https://download-installer.cdn.mozilla.net/pub/thunderbird/releases"

log() { echo "fetch-thunderbird: $*" >&2; }

case "$TB_VERSION" in
  latest|esr-latest)
    product=thunderbird-latest; [ "$TB_VERSION" = esr-latest ] && product=thunderbird-esr-latest
    loc=$(curl -fsSI -o /dev/null -w '%{redirect_url}' "https://download.mozilla.org/?product=$product&os=linux64&lang=$TB_LOCALE")
    # .../releases/157.0/linux-x86_64/en-US/thunderbird-157.0.tar.xz
    TB_VERSION=$(printf '%s' "$loc" | sed -E 's#.*/releases/([^/]+)/linux-.*#\1#')
    [ -n "$TB_VERSION" ] || { log "could not resolve $product"; exit 1; }
    log "$product resolves to $TB_VERSION"
    ;;
esac

DEST="$CACHE/thunderbird-$TB_VERSION"
URL="${TB_URL:-$BASE/$TB_VERSION/linux-x86_64/$TB_LOCALE/thunderbird-$TB_VERSION.tar.xz}"

if [ -x "$DEST/thunderbird" ] && [ -f "$DEST/.unpacked" ]; then
  echo "$DEST/thunderbird"
  exit 0
fi

mkdir -p "$CACHE"
tarball="$CACHE/thunderbird-$TB_VERSION.tar.xz"
if [ ! -s "$tarball" ]; then
  log "downloading $URL"
  curl -fsSL --retry 3 -o "$tarball.part" "$URL" >&2
  mv "$tarball.part" "$tarball"
fi
log "unpacking to $DEST"
tmp="$DEST.tmp.$$"
mkdir -p "$tmp"
tar -xJf "$tarball" -C "$tmp" --strip-components=1
[ -x "$tmp/thunderbird" ] || { log "tarball did not contain thunderbird/thunderbird"; exit 1; }
touch "$tmp/.unpacked"
if [ -d "$DEST" ]; then mv "$DEST" "$DEST.old.$$" && rm -rf "$DEST.old.$$"; fi
mv "$tmp" "$DEST"
echo "$DEST/thunderbird"

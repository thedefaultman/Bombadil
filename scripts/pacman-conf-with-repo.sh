#!/usr/bin/env bash
# Write a pacman.conf for the image build: SRC with Bombadil's repository, served from a local directory, put
# in above [core]. The image's own /etc/pacman.conf is the pacman package's, and a migrate step puts the
# repository into that one. iso/pacman.conf stays as it is, because it is also read by builds that have no repo.
#
#   scripts/pacman-conf-with-repo.sh SRC DEST REPO_DIR
set -euo pipefail

[[ $# == 3 ]] || { echo "usage: $0 SRC DEST REPO_DIR" >&2; exit 2; }
src="$1" dest="$2" repo="$3"
repo="$(cd "$repo" && pwd)"

# Optional TrustAll: the package is not signed yet, and it comes from a directory this same build just wrote.
awk -v repo="$repo" '
  !done && /^\[core\]/ {
    print "[bombadil]"; print "SigLevel = Optional TrustAll"; print "Server = file://" repo; print ""; done = 1
  }
  { print }
' "$src" > "$dest"
grep -q '^\[bombadil\]' "$dest" || { echo "Error: $src has no [core] section to put the repository above" >&2; exit 1; }

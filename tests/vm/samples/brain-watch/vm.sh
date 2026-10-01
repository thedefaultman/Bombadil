#!/usr/bin/env bash
# Run the brain watcher's checks on Arch's own kernel, on the installed btrfs layout:
# stages this folder and the repo's src/bombadil into a temp dir (the VM sees it as /test)
# and boots tests/vm/btrfs-kernel.sh on it. Extra arguments go to the harness.
#   tests/vm/samples/brain-watch/vm.sh
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../../../.." && pwd)"
stage="$(mktemp -d)"; chmod 755 "$stage"   # uid 1000 in the VM reads it
trap 'rm -rf "$stage"' EXIT
cp -a "$here/." "$stage/"
mkdir -p "$stage/src"
cp -a "$root/src/bombadil" "$stage/src/"
find "$stage/src" -name __pycache__ -prune -exec rm -rf {} +
"$root/tests/vm/btrfs-kernel.sh" "$stage" "$@"

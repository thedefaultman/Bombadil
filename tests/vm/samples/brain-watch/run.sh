#!/bin/bash
# Inside the btrfs VM (tests/vm/btrfs-kernel.sh), as root: does bombadil-brain-watch see what
# the brain needs on Arch's real kernel? Start it with tests/vm/samples/brain-watch/vm.sh,
# which puts the repo's src/bombadil at /test/src.
if [[ ! -d /test/src/bombadil/brain ]]; then
  echo "FAIL: no /test/src/bombadil: run tests/vm/samples/brain-watch/vm.sh, not the harness directly"
  exit 2
fi
echo "kernel $(uname -r), $(btrfs --version | head -1), $(python3 --version)"
findmnt -t btrfs -o TARGET,SOURCE,OPTIONS
exec python3 /test/check.py

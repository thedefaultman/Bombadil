#!/usr/bin/env bash
# Run Bombadil on any existing Hyprland desktop (for development): agentd + the bar,
# with paths pointed at this checkout and no snapshots.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$root/bin:$PATH"
export BOMBADIL_PROVIDER="${BOMBADIL_PROVIDER:-fake}"
export BOMBADIL_SHARE="$root/share"
"$root/bin/agentd" &
trap 'kill %1 2>/dev/null || true' EXIT
sleep 0.5
bombadil-shell

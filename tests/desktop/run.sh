#!/usr/bin/env bash
# The bar, agentd and the real Claude Code CLI in a headless sway session, driven by keystrokes
# against a scripted Anthropic API (fake_api.py). Screenshots, logs and a pass/fail list land in
# out/desktop. Covers what runs without Hyprland; the Super binds and how Hyprland focuses the drawer
# need a real session (the VM smoke).
#
#   tests/desktop/run.sh     needs docker and a self-contained `claude` binary (CLAUDE_BIN=...)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
claude="$(readlink -f "${CLAUDE_BIN:-$(command -v claude)}")"
out="${OUT:-$root/out/desktop}"
rm -rf "$out"; mkdir -p "$out"
if ! docker image inspect bombadil-desktop-test >/dev/null 2>&1; then
  # Behind a proxy: export HTTPS_PROXY and BOMBADIL_HOST_NET=1, as for scripts/build-in-container.sh.
  build=(--build-arg "HTTPS_PROXY=${HTTPS_PROXY:-}" --build-arg "https_proxy=${HTTPS_PROXY:-}"
         --build-arg "HTTP_PROXY=${HTTP_PROXY:-}" --build-arg "http_proxy=${HTTP_PROXY:-}")
  [[ "${BOMBADIL_HOST_NET:-}" ]] && build+=(--network host)
  docker build -q "${build[@]}" -t bombadil-desktop-test "$here" >/dev/null
fi
docker run --rm -v "$root:/repo:ro" -v "$here:/e2e:ro" -v "$out:/out" -v "$claude:/opt/claude:ro" \
  bombadil-desktop-test bash /e2e/inside.sh
grep -q "DONE pass=[0-9]* fail=0" "$out/driver.log"

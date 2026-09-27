#!/usr/bin/env bash
# Build the ISO from any Linux with Docker (or Podman): runs scripts/build-iso.sh inside an
# archlinux container with archiso installed. Output lands in ./out like the native build.
#
#   scripts/build-in-container.sh
#
# Behind an HTTP(S) proxy, export HTTPS_PROXY (and SSL_CERT_FILE for a private CA) and set
# BOMBADIL_HOST_NET=1 if the proxy listens on localhost; both are passed through to pacman.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
runtime="${CONTAINER_RUNTIME:-$(command -v docker || command -v podman)}"
image="${BOMBADIL_BUILD_IMAGE:-archlinux:base}"
mkdir -p "$root/out"

args=(--rm --privileged -v "$root:/src" -w /src -e SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(git -C "$root" log -1 --format=%ct 2>/dev/null || date +%s)}")
[[ "${BOMBADIL_HOST_NET:-}" ]] && args+=(--network host)
for v in HTTP_PROXY HTTPS_PROXY http_proxy https_proxy NO_PROXY no_proxy; do
  [[ "${!v:-}" ]] && args+=(-e "$v=${!v}")
done
if [[ "${SSL_CERT_FILE:-}" ]]; then
  args+=(-v "$SSL_CERT_FILE:/etc/bombadil-ca.crt:ro" -e SSL_CERT_FILE=/etc/bombadil-ca.crt -e CURL_CA_BUNDLE=/etc/bombadil-ca.crt)
fi
[[ -d "${PACMAN_CACHE:-}" ]] && args+=(-v "$PACMAN_CACHE:/var/cache/pacman/pkg")

exec "$runtime" run "${args[@]}" "$image" bash -euo pipefail -c '
  pacman -Syu --noconfirm --needed archiso git
  WORK=/tmp/bombadil-work OUT=/src/out /src/scripts/build-iso.sh
'

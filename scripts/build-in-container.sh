#!/usr/bin/env bash
# Build the ISO from any Linux with Docker (or Podman): runs scripts/build-iso.sh inside an
# archlinux container with archiso installed. Output lands in ./out like the native build.
#
#   scripts/build-in-container.sh
#   BOMBADIL_TEST_ENTRIES=1 scripts/build-in-container.sh     for the VM tests: adds the boot entries they use
#   scripts/build-in-container.sh package                     only the package, as a pacman repository in ./out/repo
#                                                             (BOMBADIL_COMMIT, BOMBADIL_PKGREL and BOMBADIL_PKGVER choose
#                                                             what it says it is; MODE=update of scripts/test-vm.sh uses it)
#   BOMBADIL_DEV_SERVER=1 scripts/build-in-container.sh package
#                                                             a package whose repository file also lists the test VM's
#                                                             host (10.0.2.2:8123). Only the package build is given the
#                                                             switch: an image build with BOMBADIL_TEST_ENTRIES=1 sets it
#                                                             itself, and one without is a release, which never lists it
#
# Behind an HTTP(S) proxy, export HTTPS_PROXY (and SSL_CERT_FILE for a private CA) and set
# BOMBADIL_HOST_NET=1 if the proxy listens on localhost; both are passed through to pacman.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
runtime="${CONTAINER_RUNTIME:-$(command -v docker || command -v podman)}"
image="${BOMBADIL_BUILD_IMAGE:-archlinux:base}"
mkdir -p "$root/out"

args=(--rm --privileged -v "$root:/src" -w /src -e SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(git -C "$root" log -1 --format=%ct 2>/dev/null || date +%s)}")
# The commit is read here: inside the container the checkout belongs to another user, and git refuses to read it.
args+=(-e BOMBADIL_COMMIT="${BOMBADIL_COMMIT:-$(git -C "$root" rev-parse --short HEAD 2>/dev/null || echo unknown)}")
# The package's release number counts the commits, so each build of a later commit is an update of the one before.
args+=(-e BOMBADIL_PKGREL="${BOMBADIL_PKGREL:-$(git -C "$root" rev-list --count HEAD 2>/dev/null || echo 1)}")
[[ "${BOMBADIL_PKGVER:-}" ]] && args+=(-e BOMBADIL_PKGVER="$BOMBADIL_PKGVER")
[[ "${BOMBADIL_HOST_NET:-}" ]] && args+=(--network host)
[[ "${BOMBADIL_TEST_ENTRIES:-}" ]] && args+=(-e BOMBADIL_TEST_ENTRIES="$BOMBADIL_TEST_ENTRIES")
# The switch goes only to the build of a package: an image build decides it from BOMBADIL_TEST_ENTRIES (see build-iso.sh),
# so one left in the shell cannot reach a release.
[[ "${BOMBADIL_DEV_SERVER:-}" && "${1:-iso}" == package ]] && args+=(-e BOMBADIL_DEV_SERVER="$BOMBADIL_DEV_SERVER")
for v in HTTP_PROXY HTTPS_PROXY http_proxy https_proxy NO_PROXY no_proxy; do
  [[ "${!v:-}" ]] && args+=(-e "$v=${!v}")
done
if [[ "${SSL_CERT_FILE:-}" ]]; then
  args+=(-v "$SSL_CERT_FILE:/etc/bombadil-ca.crt:ro" -e SSL_CERT_FILE=/etc/bombadil-ca.crt -e CURL_CA_BUNDLE=/etc/bombadil-ca.crt -e NODE_EXTRA_CA_CERTS=/etc/bombadil-ca.crt)
fi
[[ -d "${PACMAN_CACHE:-}" ]] && args+=(-v "$PACMAN_CACHE:/var/cache/pacman/pkg")

case "${1:-iso}" in
  iso) steps='
    pacman -Syu --noconfirm --needed archiso git nodejs npm fakeroot binutils
    WORK=/tmp/bombadil-work OUT=/src/out /src/scripts/build-iso.sh
  ' ;;
  package) steps='
    pacman -Syu --noconfirm --needed fakeroot binutils
    WORK=/tmp/bombadil-work /src/scripts/build-package.sh /src/out/repo
  ' ;;
  *) echo "usage: $0 [package]" >&2; exit 2 ;;
esac

exec "$runtime" run "${args[@]}" "$image" bash -euo pipefail -c "$steps"

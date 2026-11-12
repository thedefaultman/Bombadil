#!/usr/bin/env bash
# Build the bombadil package into a pacman repository directory: the package, bombadil.db and bombadil.files,
# and nothing else of Bombadil's. build-iso.sh uses it for the image; the laptop's update command uses it to
# serve a newer build.
#
#   scripts/build-package.sh REPO_DIR
#
# Environment:
#   BOMBADIL_COMMIT    the commit in the VERSION file (git cannot read a checkout that another user owns, as in a container)
#   BOMBADIL_PKGVER    the package version, YYYY.MM.DD (default: the UTC day of SOURCE_DATE_EPOCH)
#   BOMBADIL_PKGREL    the release number; a build with a higher one is an update (default 1)
#   BOMBADIL_DEV_SERVER  set for a build that a test VM on a laptop installs or updates from: its pacman repository file
#                      also lists the VM's host (10.0.2.2:8123), first. Never set for a release (see stage_tree).
#   SOURCE_DATE_EPOCH  the build time, for file times and the default version
#   WORK               scratch space (default /tmp/bombadil-work)
#
# Run as root, it makes the system user bombadil-build (makepkg will not run as root) and deletes the host's cached
# bombadil-* packages (the cache, never an installed package). Both are meant for a throwaway container; on a
# native host they stay.
#
# Sourced by the tests, which call stage_tree; run directly it builds.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# The package version a build time gets: its UTC day. The local day would depend on the host's time zone, and a host
# west of UTC would name an update older than the package it updates (pkgver is compared before pkgrel).
day_of() {  # day_of EPOCH
  date -u --date="@$1" +%Y.%m.%d
}

# The package's files laid out as they are on a machine: packaging/bombadil/root, and the tree at
# /usr/share/bombadil with its VERSION. Nothing a running system wrote (bytecode) goes in.
stage_tree() {  # stage_tree DEST VERSION_LINE
  local stage="$1" version="$2" share conf tmp
  share="$stage/usr/share/bombadil"
  rm -rf "$stage"; mkdir -p "$stage"
  cp -a "$root/packaging/bombadil/root/." "$stage/"
  # A test VM on a laptop is served from its host, which QEMU and WSL guests see at 10.0.2.2, so a build made for one
  # lists that first. A release must not: the file is trusted without a signature, and whatever answers at that
  # address on the network a machine happens to be on would be a source of root packages for it. An update built for
  # such a VM carries the same file, or pacman replaces the unmodified copy on the disk and drops the line.
  if [[ -n "${BOMBADIL_DEV_SERVER:-}" ]]; then
    conf="$stage/etc/pacman.d/bombadil.conf"
    tmp="$(awk '{ print } /^SigLevel = / { print "# The host of a test VM, from a build made for one. It is tried first."
                                           print "Server = http://10.0.2.2:8123" }' "$conf")"
    printf '%s\n' "$tmp" > "$conf"
    grep -qx 'Server = http://10.0.2.2:8123' "$conf" || { echo "Error: no SigLevel line in $conf to put the host after" >&2; return 1; }
  fi
  mkdir -p "$share"
  tar -C "$root" --exclude=__pycache__ --exclude='*.pyc' -cf - bin src shell share install \
    | tar -C "$share" --no-same-owner -xf -
  cp "$root/iso/packages.x86_64" "$share/packages.x86_64"
  echo "$version" > "$share/VERSION"
}

build_package() {
  local repo="$1" work="${WORK:-/tmp/bombadil-work}" epoch ver rel commit stage pkg out
  mkdir -p "$repo"; repo="$(cd "$repo" && pwd)"
  epoch="${SOURCE_DATE_EPOCH:-$(date +%s)}"
  ver="${BOMBADIL_PKGVER:-$(day_of "$epoch")}"
  rel="${BOMBADIL_PKGREL:-1}"
  commit="${BOMBADIL_COMMIT:-$(git -C "$root" rev-parse --short HEAD 2>/dev/null || echo unknown)}"
  stage="$work/pkgstage" pkg="$work/pkg" out="$work/pkg/out"

  mkdir -p "$work"; chmod 755 "$work"
  rm -rf "$pkg"; mkdir -p "$pkg/home" "$out" "$pkg/build"
  # What the image and the update say this build is: the date and the commit.
  stage_tree "$stage" "$ver ($commit)"
  cp "$root/packaging/bombadil/PKGBUILD" "$root/packaging/bombadil/bombadil.install" "$pkg/"
  chmod 755 "$pkg"

  # pacman will not use a package file it already holds under the same name, and a download is skipped when
  # the cache has it: an older build with this version and release would be installed in place of this one.
  rm -f /var/cache/pacman/pkg/bombadil-*.pkg.tar.* 2>/dev/null || true

  local build=(env SOURCE_DATE_EPOCH="$epoch" BOMBADIL_STAGE="$stage" BOMBADIL_PKGVER="$ver" BOMBADIL_PKGREL="$rel"
               PKGDEST="$out" BUILDDIR="$pkg/build" HOME="$pkg/home")
  if [[ $EUID -eq 0 ]]; then
    # makepkg will not run as root: a throwaway user builds, and the package still holds root's name for every file.
    id bombadil-build >/dev/null 2>&1 \
      || useradd --system --no-create-home --home-dir "$pkg/home" --shell /usr/bin/nologin bombadil-build
    chown -R bombadil-build: "$stage" "$pkg"
    (cd "$pkg" && runuser -u bombadil-build -- "${build[@]}" makepkg --nodeps --noconfirm --force --nocolor)
  else
    (cd "$pkg" && "${build[@]}" makepkg --nodeps --noconfirm --force --nocolor)
  fi

  # Only this build is in the repository: a repository that also holds an older one lets pacman pick either.
  rm -f "$repo"/bombadil-*.pkg.tar.* "$repo"/bombadil.db* "$repo"/bombadil.files*
  local built="" f
  for f in "$out/bombadil-$ver-$rel-any.pkg.tar."*; do [[ "$f" == *.sig ]] || built="$f"; done
  [[ -f "$built" ]] || { echo "Error: makepkg left no package in $out" >&2; return 1; }
  cp "$built" "$repo/"
  repo-add --quiet "$repo/bombadil.db.tar.zst" "$repo/$(basename "$built")"
  # The cache user (DownloadUser = alpm) reads the repository, so it has to be open to everyone.
  chmod 755 "$repo"; chmod 644 "$repo"/*.tar.*
  echo "Package bombadil $ver-$rel ($commit) is in $repo${BOMBADIL_DEV_SERVER:+ (a test build: its repository file lists the host of the VM first)}"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  [[ $# == 1 ]] || { echo "usage: $0 REPO_DIR" >&2; exit 2; }
  build_package "$1"
fi

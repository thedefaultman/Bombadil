#!/usr/bin/env bash
# os-release belongs to filesystem. The logo it names is the one the system shows for itself: Arch's own mark
# is replaced by Bombadil's, which the package ships in the icon theme. A LOGO= the person set by hand stays.
# ID and ID_LIKE are not touched.
set -euo pipefail

root="${BOMBADIL_ROOT:-}"; root="${root%/}"
file="$root/usr/lib/os-release"
# systemd reads /etc/os-release first, so a real file there is the one that counts.
[[ -f "$root/etc/os-release" && ! -L "$root/etc/os-release" ]] && file="$root/etc/os-release"
[[ -f "$file" ]] || { echo "no $file"; exit 1; }

if grep -qx 'LOGO=bombadil' "$file"; then
  echo already; exit 0
fi
if grep -q '^LOGO=' "$file" && ! grep -qx 'LOGO=archlinux-logo' "$file"; then
  echo "already: $file names a logo of its own"; exit 0
fi

tmp="$(mktemp "$file.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
if grep -q '^LOGO=' "$file"; then
  sed 's/^LOGO=archlinux-logo$/LOGO=bombadil/' "$file" > "$tmp"
else
  { cat "$file"; [[ -z "$(tail -c1 "$file")" ]] || echo; echo "LOGO=bombadil"; } > "$tmp"
fi
chmod --reference="$file" "$tmp"
chown --reference="$file" "$tmp" 2>/dev/null || true
mv -f "$tmp" "$file"
echo changed

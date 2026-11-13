#!/usr/bin/env bash
# /etc/environment belongs to pam. The browser command moved from /usr/local/bin to /usr/bin with the package,
# so the old path in an installed system's file no longer exists. Only that exact line is changed.
set -euo pipefail

root="${BOMBADIL_ROOT:-}"; root="${root%/}"
file="$root/etc/environment"
[[ -f "$file" ]] || { echo already; exit 0; }

if ! grep -qx 'BROWSER=/usr/local/bin/bombadil-browser' "$file"; then
  echo already; exit 0
fi

tmp="$(mktemp "$file.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
sed 's|^BROWSER=/usr/local/bin/bombadil-browser$|BROWSER=/usr/bin/bombadil-browser|' "$file" > "$tmp"
chmod --reference="$file" "$tmp"
chown --reference="$file" "$tmp" 2>/dev/null || true
mv -f "$tmp" "$file"
echo changed

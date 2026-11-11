#!/usr/bin/env bash
# pacman.conf belongs to pacman. Bombadil's repository goes in above [core], so its package is found first;
# the list of servers is in /etc/pacman.d/bombadil.conf, which the bombadil package owns.
set -euo pipefail

root="${BOMBADIL_ROOT:-}"; root="${root%/}"
conf="$root/etc/pacman.conf"
[[ -f "$conf" ]] || { echo "no $conf"; exit 1; }

if grep -qE '^[[:space:]]*\[bombadil\]' "$conf"; then
  echo already; exit 0
fi
grep -qE '^[[:space:]]*\[core\]' "$conf" || { echo "no [core] section in $conf, so nothing was changed"; exit 1; }

tmp="$(mktemp "$conf.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
awk '
  !done && /^[[:space:]]*\[core\]/ { print "[bombadil]"; print "Include = /etc/pacman.d/bombadil.conf"; print ""; done = 1 }
  { print }
' "$conf" > "$tmp"
chmod --reference="$conf" "$tmp"
chown --reference="$conf" "$tmp" 2>/dev/null || true
mv -f "$tmp" "$conf"
echo changed

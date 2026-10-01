#!/usr/bin/env bash
# Run Bombadil on any existing Hyprland desktop (for development): agentd + the bar,
# with paths pointed at this checkout and no snapshots.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$root/bin:$PATH"
export BOMBADIL_PROVIDER="${BOMBADIL_PROVIDER:-fake}"
export BOMBADIL_SHARE="$root/share"
# Mail runs on its fake engine here (sample mailboxes, no Thunderbird, no account), kept in a scratch
# folder so nothing lands in the mail of a Bombadil you also use. agentd and the service share the press
# log, so it is set before either starts. BOMBADIL_MAIL_ENGINE=thunderbird (anything but fake) runs the
# real service on the real state.
if [[ "${BOMBADIL_MAIL_ENGINE:-fake}" == fake ]]; then
  mail="$(mktemp -d)"
  export BOMBADIL_MAIL_ENGINE=fake BOMBADIL_MAIL_DB="$mail/mail.db" BOMBADIL_MAIL_FILES="$mail/files" \
    BOMBADIL_PRESS_LOG="$mail/presses.jsonl"
fi
trap 'kill $(jobs -p) 2>/dev/null || true; [[ -z "${mail:-}" ]] || rm -rf "$mail"' EXIT
"$root/bin/agentd" &
"$root/bin/bombadil-mail" &
sleep 0.5
bombadil-shell

#!/usr/bin/env bash
# run-thunderbird.sh: start / stop the Bombadil Mail lab.
#
#   run-thunderbird.sh start     Xvfb (or $DISPLAY) + local mail server + fresh profile + Thunderbird + add-on
#   run-thunderbird.sh stop      kill Thunderbird, the mail server, Xvfb/openbox (state kept for inspection)
#   run-thunderbird.sh clean     stop, then delete the lab dir (and anything a LAB_LOAD mode put in /etc)
#   run-thunderbird.sh restart   stop Thunderbird only and start it again on the SAME profile (servers/display kept)
#   run-thunderbird.sh status    what is running, add-on state
#   run-thunderbird.sh shot [f]  screenshot of the lab display (PNG)
#   run-thunderbird.sh env       print the lab's environment (source it: eval "$(run-thunderbird.sh env)")
#
# Everything lives under $BOMBADIL_LAB_DIR (default /tmp/bombadil-lab):
#   profile/  fresh Thunderbird profile (user.js + logins.json + extensions/)   home/  HOME of Thunderbird
#   mail/     Dovecot + SMTP sink (see mailserver.py)     tb.log  stdout/stderr of Thunderbird
#   host.log  what the native-messaging host saw          hello.json  first frame the add-on sent
#   host.sock Unix socket the native host connects to (serve it with lab_bridge.py)   env.sh  env for tests
#
# Environment knobs (all optional):
#   TB_VERSION=157.0            which Thunderbird (fetch-thunderbird.sh)        TB_DIR=<dir>  use this app dir
#   BOMBADIL_LAB_DIR=/tmp/...   lab root                                        LAB_SIZE=1280x800x24
#   DISPLAY=:0 LAB_XVFB=0       use an existing display instead of Xvfb         LAB_WM=openbox|none (default none)
#   LAB_LOAD=profile-xpi        how the add-on gets in:
#       profile-xpi   <profile>/extensions/<id>.xpi + autoDisableScopes=0           (default, works)
#       profile-dir   unpacked dir <profile>/extensions/<id>/ + same prefs
#       policy-dist   <app>/distribution/policies.json ExtensionSettings force_installed file://
#       policy-etc    /etc/thunderbird/policies/policies.json  (same policy)
#       distribution-ext  <app>/distribution/extensions/<id>.xpi   app-ext  <app>/extensions/<id>.xpi
#       features      <app>/features/<id>.xpi (system add-on dir)       none  no add-on
#   LAB_ADDON_PREFS=0|1         force extensions.autoDisableScopes=0 etc. on/off (default: on for profile-*)
#   LAB_SIGNATURES_REQUIRED=1   set xpinstall.signatures.required=true (proves the pref is honoured)
#   LAB_EXTRA_PREFS=file.js     extra user_pref lines         LAB_KIND=lab|gmail|outlook|icloud|none  account template
#   LAB_NO_QUIET=1              omit the "quiet" prefs (shows what pops up on first run)   LAB_ABLATE=pref1,pref2  omit just these
#   LAB_NO_DISPLAY=1            no Xvfb and DISPLAY unset (use with LAB_HEADLESS=1)
#   LAB_HEADLESS=1              pass --headless               LAB_MARIONETTE=1  pass --marionette (port 2828)
#   LAB_IMAP_PORT=1143 LAB_SMTP_PORT=1025   ports of the local mail server (lets two labs run side by side with different
#                               BOMBADIL_LAB_DIR)
#   LAB_NO_SERVER=1             do not start the mail server  LAB_NO_WAIT=1     do not wait for the add-on
#   LAB_APP_COPY=1              work on a hard-linked copy of the app dir (implied by policy-dist/distribution-ext/...)
#   LAB_POLICY_JSON=file.json   write this as <app>/distribution/policies.json (implies LAB_APP_COPY)
#   LAB_XPI=file.xpi            add-on to install (default: built from extension/)   LAB_SEED=dir  (default ./seed)
#   LAB_NM_DIR=dir              where the native-messaging manifest goes (default $LAB/home/.mozilla/native-messaging-hosts)
#   LAB_WAIT=60                 seconds to wait for the add-on's hello
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB="${BOMBADIL_LAB_DIR:-/tmp/bombadil-lab}"
EXT_ID="bombadil-mail-lab@bombadil.lab"
CMD="${1:-start}"

log() { echo "run-thunderbird: $*" >&2; }
alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }
pidof_file() { [ -f "$1" ] && cat "$1" || true; }

safe_lab() {
  case "$LAB" in
    /tmp/*|/var/tmp/*|"$HOME"/.cache/*) ;;
    *) log "refusing to manage lab dir outside /tmp or ~/.cache: $LAB"; exit 2 ;;
  esac
}

do_stop() {
  safe_lab
  local p
  p="$(pidof_file "$LAB/tb.pid")"
  if alive "$p"; then
    kill "$p" 2>/dev/null || true
    for _ in $(seq 1 50); do alive "$p" || break; sleep 0.2; done
    alive "$p" && kill -9 "$p" 2>/dev/null || true
  fi
  # thunderbird -> thunderbird-bin child
  if [ -f "$LAB/tb.pgid" ]; then kill -- "-$(cat "$LAB/tb.pgid")" 2>/dev/null || true; fi
  p="$(pidof_file "$LAB/mail/server.pid")"; alive "$p" && kill "$p" 2>/dev/null || true
  p="$(pidof_file "$LAB/wm.pid")"; alive "$p" && kill "$p" 2>/dev/null || true
  p="$(pidof_file "$LAB/xvfb.pid")"; alive "$p" && kill "$p" 2>/dev/null || true
  rm -f "$LAB/host.sock"
  return 0
}

do_clean() {
  safe_lab
  do_stop
  rm -f /etc/thunderbird/policies/policies.json 2>/dev/null || true
  rmdir /etc/thunderbird/policies /etc/thunderbird 2>/dev/null || true
  [ -d "$LAB" ] && rm -rf "$LAB"
  log "cleaned $LAB"
}

wait_file() { # file seconds
  local i
  for i in $(seq 1 $(( $2 * 10 ))); do [ -s "$1" ] && return 0; sleep 0.1; done
  return 1
}

launch_tb() {
  # shellcheck disable=SC1091
  . "$LAB/launch.sh"
  DISPLAY="$(cat "$LAB/display")"
  date +%s.%N > "$LAB/start.time"
  local dispenv=(-u DISPLAY); [ -n "$DISPLAY" ] && dispenv=(DISPLAY="$DISPLAY")
  env HOME="$LAB/home" "${dispenv[@]}" BOMBADIL_LAB_DIR="$LAB" MOZ_CRASHREPORTER_DISABLE=1 \
      MOZ_CRASHREPORTER_NO_REPORT=1 NO_AT_BRIDGE=1 \
      setsid "$TB_BIN" "${TB_ARGS[@]}" >>"$LAB/tb.log" 2>&1 &
  echo $! > "$LAB/tb.pid"; echo $! > "$LAB/tb.pgid"
}

do_restart() {
  safe_lab
  local p
  p="$(pidof_file "$LAB/tb.pid")"
  if alive "$p"; then
    kill "$p" 2>/dev/null || true
    for _ in $(seq 1 50); do alive "$p" || break; sleep 0.2; done
    alive "$p" && kill -9 "$p" 2>/dev/null || true
  fi
  kill -- "-$(cat "$LAB/tb.pgid")" 2>/dev/null || true
  sleep 1
  rm -f "$LAB/hello.json"
  launch_tb
  log "restarted thunderbird, pid $(cat "$LAB/tb.pid")"
  if [ "${LAB_NO_WAIT:-0}" != 1 ]; then
    wait_file "$LAB/hello.json" "${LAB_WAIT:-60}" && log "add-on connected after $(python3 -c "import time;print('%.1f' % (time.time()-float(open('$LAB/start.time').read())))") s" || { log "add-on did not reconnect"; exit 3; }
  fi
}

do_start() {
  safe_lab
  do_stop
  [ -d "$LAB" ] && rm -rf "$LAB"
  mkdir -p "$LAB/home" "$LAB/profile"
  chmod 755 "$LAB"   # the mail server runs as `nobody` when started as root and must traverse this

  TB_BIN="${TB_DIR:+$TB_DIR/thunderbird}"
  TB_BIN="${TB_BIN:-$("$HERE/fetch-thunderbird.sh")}"
  APP="$(dirname "$TB_BIN")"
  LOAD="${LAB_LOAD:-profile-xpi}"
  XPI="${LAB_XPI:-$(python3 "$HERE/build_xpi.py")}"

  # ---- display ------------------------------------------------------------------------------
  if [ "${LAB_NO_DISPLAY:-0}" = 1 ]; then
    unset DISPLAY
  elif [ "${LAB_XVFB:-auto}" = 1 ] || { [ "${LAB_XVFB:-auto}" = auto ] && [ -z "${DISPLAY:-}" ]; }; then
    : > "$LAB/xvfb.display"
    Xvfb -screen 0 "${LAB_SIZE:-1280x800x24}" -nolisten tcp -displayfd 3 3>"$LAB/xvfb.display" >"$LAB/xvfb.log" 2>&1 &
    echo $! > "$LAB/xvfb.pid"
    wait_file "$LAB/xvfb.display" 10 || { log "Xvfb did not start"; cat "$LAB/xvfb.log" >&2; exit 1; }
    export DISPLAY=":$(tr -d '\n' < "$LAB/xvfb.display")"
  fi
  echo "${DISPLAY:-}" > "$LAB/display"
  if [ "${LAB_WM:-none}" = openbox ]; then
    openbox >"$LAB/wm.log" 2>&1 &
    echo $! > "$LAB/wm.pid"; sleep 0.5
  fi

  # ---- mail server ----------------------------------------------------------------------------
  if [ "${LAB_NO_SERVER:-0}" != 1 ]; then
    python3 "$HERE/mailserver.py" serve --dir "$LAB/mail" --imap-port "${LAB_IMAP_PORT:-1143}" --smtp-port "${LAB_SMTP_PORT:-1025}" --seed "${LAB_SEED:-$HERE/seed}" >"$LAB/mail-server.log" 2>&1 &
    wait_file "$LAB/mail/ready" 20 || { log "mail server did not come up"; cat "$LAB/mail-server.log" >&2; exit 1; }
  fi

  # ---- app dir: modes that modify the application directory work on a hard-linked copy ------------------
  need_copy=${LAB_APP_COPY:-0}
  case "$LOAD" in policy-dist|distribution-ext|app-ext|features) need_copy=1 ;; esac
  [ "${LAB_AUTOCONFIG:-0}" = 1 ] || [ -n "${LAB_POLICY_JSON:-}" ] && need_copy=1
  if [ "$need_copy" = 1 ]; then cp -al "$APP" "$LAB/app"; APP="$LAB/app"; TB_BIN="$APP/thunderbird"; fi
  if [ -n "${LAB_POLICY_JSON:-}" ]; then mkdir -p "$APP/distribution"; cp "$LAB_POLICY_JSON" "$APP/distribution/policies.json"; fi

  case "$LOAD" in
    policy-dist|policy-etc)
      pol="{\"policies\":{\"ExtensionSettings\":{\"$EXT_ID\":{\"installation_mode\":\"force_installed\",\"install_url\":\"file://$XPI\"}}}}"
      if [ "$LOAD" = policy-dist ]; then mkdir -p "$APP/distribution"; [ -n "${LAB_POLICY_JSON:-}" ] || echo "$pol" > "$APP/distribution/policies.json"
      else mkdir -p /etc/thunderbird/policies; echo "$pol" > /etc/thunderbird/policies/policies.json; fi ;;
    distribution-ext) mkdir -p "$APP/distribution/extensions"; cp "$XPI" "$APP/distribution/extensions/$EXT_ID.xpi" ;;
    app-ext)          mkdir -p "$APP/extensions"; cp "$XPI" "$APP/extensions/$EXT_ID.xpi" ;;
    features)         mkdir -p "$APP/features"; cp "$XPI" "$APP/features/$EXT_ID.xpi" ;;
  esac

  # ---- native messaging host manifest (Thunderbird 157 Linux searches ONLY ~/.mozilla/native-messaging-hosts
  #      and /usr/lib/mozilla/native-messaging-hosts; HOME is $LAB/home so the lab stays self-contained) ---------
  NM="${LAB_NM_DIR:-$LAB/home/.mozilla/native-messaging-hosts}"
  mkdir -p "$LAB/host" "$NM"
  cat > "$LAB/host/bombadil_mail_host.sh" <<EOS
#!/bin/sh
exec python3 "$HERE/host/bombadil_mail_host.py" --sock "$LAB/host.sock" --log "$LAB/host.log" --hello-file "$LAB/hello.json" "\$@"
EOS
  chmod +x "$LAB/host/bombadil_mail_host.sh"
  cat > "$NM/bombadil_mail.json" <<EOS
{"name":"bombadil_mail","description":"Bombadil Mail lab host","path":"$LAB/host/bombadil_mail_host.sh","type":"stdio","allowed_extensions":["$EXT_ID"]}
EOS

  # ---- profile ----------------------------------------------------------------------------------
  pl_load="$LOAD"; case "$LOAD" in profile-xpi|profile-dir|none) ;; *) pl_load=other ;; esac
  addon_prefs="${LAB_ADDON_PREFS:-}"
  [ -z "$addon_prefs" ] && case "$LOAD" in profile-xpi|profile-dir) addon_prefs=1 ;; *) addon_prefs=0 ;; esac
  extra="$LAB/extra-prefs.js"; : > "$extra"
  [ -n "${LAB_EXTRA_PREFS:-}" ] && cat "$LAB_EXTRA_PREFS" >> "$extra"
  [ "${LAB_SIGNATURES_REQUIRED:-0}" = 1 ] && echo 'user_pref("xpinstall.signatures.required", true);' >> "$extra"
  (cd "$HERE" && python3 - <<PYEOF
import make_profile
make_profile.write_profile("$LAB/profile", kind="${LAB_KIND:-lab}", load="$pl_load", xpi="$XPI",
                           imap_port=${LAB_IMAP_PORT:-1143}, smtp_port=${LAB_SMTP_PORT:-1025},
                           nss_dir="$APP", extra_user_js="$extra", addon_prefs=bool(int("$addon_prefs")),
                           quiet=not bool(int("${LAB_NO_QUIET:-0}")), ablate=tuple(x for x in "${LAB_ABLATE:-}".split(",") if x))
PYEOF
  )
  if [ "${LAB_AUTOCONFIG:-0}" = 1 ]; then  # see FINDINGS Q1(d): AutoConfig locking/setting prefs
    mkdir -p "$APP/defaults/pref"
    printf 'pref("general.config.filename", "thunderbird.cfg");\npref("general.config.obscure_value", 0);\n' > "$APP/defaults/pref/autoconfig.js"
    cp "${LAB_AUTOCONFIG_CFG:?set LAB_AUTOCONFIG_CFG}" "$APP/thunderbird.cfg"
  fi

  # ---- launch -------------------------------------------------------------------------------------
  args=(-no-remote -profile "$LAB/profile")
  [ "${LAB_HEADLESS:-0}" = 1 ] && args+=(--headless)
  [ "${LAB_MARIONETTE:-0}" = 1 ] && args+=(--marionette)
  { printf 'TB_BIN=%q\nTB_ARGS=(' "$TB_BIN"; printf '%q ' "${args[@]}"; printf ')\n'; } > "$LAB/launch.sh"
  launch_tb

  cat > "$LAB/env.sh" <<EOS
export BOMBADIL_LAB_DIR="$LAB"
export DISPLAY="${DISPLAY:-}"
export TB_APP_DIR="$APP"
export LAB_PROFILE="$LAB/profile"
export LAB_HOST_SOCK="$LAB/host.sock"
export LAB_IMAP="127.0.0.1:${LAB_IMAP_PORT:-1143}"
export LAB_SMTP="127.0.0.1:${LAB_SMTP_PORT:-1025}"
export LAB_TB_PID="$(cat "$LAB/tb.pid")"
EOS
  log "thunderbird pid $(cat "$LAB/tb.pid") on DISPLAY=$DISPLAY, lab dir $LAB, load mode $LOAD"

  if [ "${LAB_NO_WAIT:-0}" != 1 ] && [ "$LOAD" != none ]; then
    if wait_file "$LAB/hello.json" "${LAB_WAIT:-60}"; then
      log "add-on connected after $(python3 -c "import time;print('%.1f' % (time.time()-float(open('$LAB/start.time').read())))") s: $(head -c 300 "$LAB/hello.json")"
    else
      log "add-on did NOT connect within ${LAB_WAIT:-60}s; see $LAB/tb.log and $LAB/host.log"
      exit 3
    fi
  fi
  echo "$LAB"
}

do_status() {
  local p
  for n in tb.pid xvfb.pid wm.pid mail/server.pid; do
    p="$(pidof_file "$LAB/$n")"
    if alive "$p"; then echo "$n $p running"; else echo "$n ${p:--} not running"; fi
  done
  [ -f "$LAB/hello.json" ] && echo "hello: $(head -c 300 "$LAB/hello.json")" || echo "hello: none yet"
  python3 - "$LAB/profile/extensions.json" <<'PYEOF' 2>/dev/null || true
import json, sys
for a in json.load(open(sys.argv[1]))["addons"]:
    if "bombadil" in a["id"]:
        print({k: a.get(k) for k in ("id", "location", "signedState", "active", "userDisabled", "appDisabled")})
PYEOF
}

case "$CMD" in
  start)  do_start ;;
  restart) do_restart ;;
  stop)   do_stop ;;
  clean)  do_clean ;;
  status) do_status ;;
  env)    cat "$LAB/env.sh" ;;
  shot)   DISPLAY="$(cat "$LAB/display")" import -window root "${2:-$LAB/shot.png}"; echo "${2:-$LAB/shot.png}" ;;
  *) sed -n '2,40p' "$0"; exit 1 ;;
esac

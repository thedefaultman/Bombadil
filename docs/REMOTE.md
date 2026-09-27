# Remote

Using the machine from another laptop or an iPhone: talk to it, finish the logins it starts,
reach a terminal, move files, and see the whole desktop when nothing else will do. This is a
design, researched 2026-09-27; nothing here is built yet.

## What it is for

Away from the machine, the owner mostly:

- runs commands, usually by asking the agent;
- finishes browser logins the machine starts (`aws sso login`, a CLI's re-auth). **The AWS
  login needs a passkey**;
- moves files in both directions;
- and only sometimes needs the whole desktop.

The owner uses an **iPhone** and a second laptop, already uses Tailscale, and **can forward a
UDP port** on the home router. Today they use Sunshine + Moonlight over Tailscale, which often
fails.

## Why not just Sunshine

Sunshine streams the monitor as 60 fps video over UDP, like a game. For that to work, capture,
encoder, input, pairing and a fast direct path all have to line up. None of the jobs above
needs a video stream. The documented ways it fails on Hyprland:

| Failure | Cause | Fix |
|---|---|---|
| Black or frozen stream | Monitor off or asleep: DPMS stops Hyprland rendering that output | Capture a headless output (below) |
| No capture, or a picker that hangs | Auto capture picks KMS, which needs `cap_sys_admin` and was broken by the 2026 privilege changes (Sunshine #5803, open), or the portal, whose Hyprland picker hangs with the monitor off (xdg-desktop-portal-hyprland #437) | `capture = wlr` |
| Service never starts | Its unit waits for `graphical-session.target`, which greetd → Hyprland never reaches | Start it from a user unit or uwsm once Hyprland is up |
| Wrong output after a change | Sunshine keeps the output name it saw at startup | Start it after the headless output exists |
| Choppy or slow from the phone | Phone on cellular sits behind the carrier's NAT, so Tailscale relays through DERP (~5–10 Mbps reported for Moonlight) | Forward UDP 41641 at home (below) |
| Pairing fails silently over the tailnet name | CSRF check since Sunshine v2026.428 | `csrf_allowed_origins=https://<host>:47990` |

Sunshine stays as an optional fast mode with those fixes preset (piece 7). The default is the
lighter pieces below, each of which works when the ones after it do not.

## The pieces

```
 iPhone / laptop ── Tailscale, direct (UDP 41641 forwarded at home) ──┐
                                                                      │
 https://<host>.<tailnet>.ts.net     tailscale serve --bg, tailnet only, Let's Encrypt cert
   /           bombadil-remote       the pill as a web app on the Home Screen, and its pushes
   /files      bombadil-remote       upload to ~/Downloads, download what you pick
   /tab        bombadil-remote       one browser tab, live (CDP screencast + input)
   /term/<s>   zellij web            a coding session by name
   /desktop    noVNC -> wayvnc       Hyprland's `remote` headless output
 ssh <host>    Tailscale SSH (+ mosh)
 share sheet   Taildrop -> ~/Downloads
```

`bombadil-remote` is a user service listening on 127.0.0.1 only. To agentd it is one more
client on the socket, like the bar or `bombadil ask`.

### 1. Reach

- **ISO packages:** tailscale (1.102 or later), mosh and wayvnc. noVNC comes packaged or
  vendored into `share/remote/` (it is MPL-2.0 JavaScript).
- **First run** gets an optional step, "Reach this machine from your phone":
  - `sudo tailscale up` prints a login URL. It opens in the browser panel through the same
    route as the provider sign-in (bombadil-browser → agentd `open_url`).
  - Then `sudo tailscale set --operator=$USER --ssh`, then the `tailscale serve --bg` lines.
  - `--bg` makes serve's config survive reboots.
- **Once, in the Tailscale admin console:** turn on MagicDNS and HTTPS certificates. Without
  them serve waits for approval. Setup says so when that happens.
- **The owner's login:** setup records it in `~/.config/bombadil/remote.toml` as
  `owner = "..."`, from `tailscale status --json` (the Self node's user).
- **Direct connections:** forward UDP 41641 on the home router to this machine, and give the
  machine a DHCP reservation so the forward keeps pointing at it.
  - From the laptop, `tailscale ping <host>` should then say `via <public ip>:41641`, not
    `via DERP(...)`. That holds even from a phone behind the carrier's NAT, because one
    directly reachable side is enough.
  - `bombadil remote doctor` wraps `tailscale netcheck` and `tailscale status` and says what
    to fix.
- **iPhone:** in the Tailscale app, set VPN On Demand to Always on Wi-Fi and cellular. The web
  app and the links in pushes then open without toggling the VPN.
- **Staying awake** while remote is on:
  - either a logind drop-in, `/etc/systemd/logind.conf.d/bombadil-remote.conf`, with
    `HandleLidSwitch=ignore`, `HandleLidSwitchExternalPower=ignore` and `IdleAction=ignore`;
  - or bombadil-remote holds
    `systemd-inhibit --what=sleep:idle:handle-lid-switch --mode=block`. The lid ignores plain
    sleep inhibitors, so the `handle-lid-switch` lock is needed.
- **Waking a machine that is already asleep** needs another always-on device on the home LAN.
  Tailscale works at layer 3 and cannot send the magic packet itself. Out of scope here.

### 2. The pill on the iPhone

- **Protocol:** a WebSocket at `/ws` carries agentd's JSON lines in both directions.
  - Clients may send `prompt`, `stop`, `unqueue`, `local` (undo), `details` and `status`.
  - `summon` and `close_details` belong to the local screen and are dropped.
  - Every broadcast goes to the phone as it does to the bar.
- **UI:** the same pill: the input at the bottom, the line above it, the reply, the queue, and
  the dots of the coding sessions once #4 lands.
  - Plain HTML and JS in `share/remote/`, with no build step, the same as the QML side.
- **iPhone Home Screen:** the owner adds the web app from Safari (manifest `display:
  standalone`). iOS 16.4 or later only allows Web Push from an app installed that way, and
  asks permission on a tap.
- **Pushes:** Web Push with VAPID keys made at setup.
  - They travel through Apple's push service, so the machine needs outbound internet but the
    app stays tailnet-only. One project (collie) does exactly this; Apple says nothing either
    way.
  - Something is pushed when:
    - a turn finishes while the owner is away;
    - a coding session asks something;
    - a login needs the owner.
  - ntfy is the fallback. Self-hosted ntfy on iOS relays via ntfy.sh's `upstream-base-url`,
    which sees only a message id and a hash.
- **Away:** set while a remote client has the web app in front (it reports
  `visibilitychange`), and cleared by local input (the pill summoned).
  - agentd carries it in `status`.
  - The provider system prompt tells the agent what changes while away: logins go to the
    phone, and links are sent rather than only opened.

### 3. Logins while away

The AWS login needs a passkey, and that decides the order:

1. **The home browser, silently.** `aws sso login` (PKCE, the default since AWS CLI 2.22.0)
   opens its page in the browser panel. If the Identity Center session in that profile is
   still valid, it finishes with nobody there.
   - bombadil-remote watches the tab over the debugging port, as `signin.py` does for the
     providers.
   - On success it pushes "AWS login done".
   - If the tab sits on a sign-in page for about 15 s, it moves to step 2.
2. **Device code, on the phone.** The agent reruns it as `aws sso login --use-device-code`.
   - The push says "Approve AWS login, code ABCD-EFGH" and links to
     `https://device.sso.<region>.amazonaws.com/`.
   - Safari opens it, and the passkey works there because it is the phone's own browser:
     iCloud Keychain, or a security key over NFC or USB-C.
   - The CLI has no setting to make device code the default (aws-cli #9098, open), so the
     agent passes the flag. A wrapper around `aws` would be surprising and is not worth it.
   - Claude Code: paste-code flow. `claude auth login` reads the code on stdin, and the web
     app gets a field for it.
   - Codex: `codex login --device-auth`, after device code is enabled in ChatGPT's security
     settings.
3. **The failed URL.** If a login was opened on the phone by mistake, the owner pastes the
   `http://127.0.0.1:<port>/…?code=…&state=…` it failed on.
   - bombadil-remote replays it with a GET to 127.0.0.1, and only on a port where a login it
     is tracking listens.
   - AWS's callback waits 10 minutes; `--redirect-port` (2.37.2) makes the port fixed.

Why the live tab (piece 4) is not the answer for this login:
- A phone's passkey reaches a desktop browser only through the hybrid transport, which checks
  Bluetooth range.
- The passkey dialog is browser UI, not page content, so a screencast never shows it.

Worth raising with whoever runs the AWS org: Identity Center's session length defaults to 8 h
and can be 15 min to 90 days. A longer one makes step 1 succeed far more often.

### 4. One browser tab, live (`/tab`)

For pages that need the owner and are not logins, instead of streaming the desktop.

- bombadil-remote attaches to one target on 127.0.0.1:9222 and runs `Page.startScreencast`:
  JPEG, quality about 60, `maxWidth` from the phone's width, every frame acked.
- **Taps** become `Input.dispatchMouseEvent`, mapped through each frame's metadata
  (`deviceWidth`, `pageScaleFactor`, `offsetTop`, scroll).
- **Typing** goes through a real, focused, hidden `<input>` so iOS shows its keyboard:
  - `input` / `compositionend` become `Input.insertText`;
  - Backspace and Enter become `Input.dispatchKeyEvent`.
- **Layout:** the same tab by default, so a page in progress is not reloaded. "Phone layout"
  reopens it in its own tab with `Emulation.setDeviceMetricsOverride({mobile: true})`, so the
  panel's tab is never reflowed.
- **Never exposed:** port 9222 itself. The WebSocket carries frames and input for one tab and
  nothing else.
- **Not in the stream:** native `<select>` popups, permission prompts, passkey dialogs and file
  pickers. For those, use `/desktop`.
- **Reference:** steel-browser's casting handler (Apache-2.0). browserless's live view is
  paid, and BrowserBox is proprietary.

### 5. Terminal

- **zellij web** (0.43 or later; the phone interface is from 0.45) on 127.0.0.1:8082, served at
  `/term`.
  - The coding sessions (#4) are already named zellij sessions, so `/term/<session>` brings
    one back by name.
  - Their config needs web sharing on and a `/term` base URL. Check the option names against
    zellij's docs when building.
  - zellij has its own token (`zellij web --create-token`), entered once per browser. It
    cannot take Tailscale's headers.
- **Tailscale SSH** for the laptop, and for Blink Shell or Termius on the iPhone, with mosh for
  cellular.
  - The tailnet policy uses `accept` for the owner's own devices. Check mode would send the
    iPhone to a browser every 12 h.
- **Optional, Claude only:** coding sessions started with `claude --remote-control` show up in
  the Claude iPhone app.
  - Needs a claude.ai login, not an API key.
  - agentd's one-turn `claude -p` runs can't be reached this way.
  - Forwarded prompts expire after 5 minutes.
  - A `config.toml` switch, off by default.

### 6. Files

- **iPhone → machine:** share sheet → Tailscale → this machine (Taildrop; turn on Send Files
  in the admin console).
  - A user unit runs `tailscale file get --loop --conflict=rename ~/Downloads`, which the
    operator user may do without sudo.
- **Machine → iPhone:** a `send_file(path)` tool in bombadil-os-mcp.
  - It runs `tailscale file cp <path> <phone>:`, with the phone's node name recorded at setup.
  - It also leaves a download link in the web app.
- **`/files` in the web app:** upload into `~/Downloads`, and download whatever the owner
  picks.
  - No third-party file server. filebrowser was archived on 2026-09-01; copyparty is the one
    to take if a full browser is ever wanted.

### 7. Desktop (`/desktop`), when nothing else will do

- **A headless output named `remote`:** `hyprctl output create headless remote`. The config
  is Lua, so modes are set with
  `hyprctl eval 'hl.monitor({output = "remote", mode = "…", position = "-9999x0", scale = 2})'`;
  `hyprctl keyword` only works with the old hyprlang config.
  - bombadil-remote sizes it from the client's viewport before connecting: iPhone portrait or
    laptop landscape.
  - The local screen is not mirrored and can stay off.
  - Never DPMS-off the captured output; that freezes the capture.
- **Capture:** wayvnc 0.10 captures `remote` with its WebSocket listener on 127.0.0.1.
  - noVNC in the web app gives the iPhone touch and an on-screen keyboard without an app.
  - The laptop uses the same page, or any VNC client through `tailscale serve --tcp`.
  - It runs over TCP, which holds up on a relayed path where UDP streaming does not.
- **The bar and the panels** have to appear on `remote` too: the Quickshell bar per screen, and
  special workspaces on the focused monitor. Check both.
- **Fast mode, optional:** Sunshine pinned to a release, with `capture = wlr`,
  `output_name = remote`, a prep command that sets the mode from
  `SUNSHINE_CLIENT_WIDTH/HEIGHT/FPS`, a user unit started after the output exists, and the
  CSRF origin from the table above.
- **To watch:** hypr-rdp (v0.1.6, MIT) is the most complete Hyprland-native option: RDP, a
  headless output per client, audio, clipboard. It is too new to be the default, and it cannot
  yet resize its output on Hyprland 0.56 with the Lua config.
- **Not a fit today:**
  - RustDesk: no remote input on Hyprland, and unattended Wayland support is a preview on
    Debian and Ubuntu only.
  - GNOME Remote Desktop and KRdp: tied to their own compositors.
  - KasmVNC: X11.

## Security

- **The stakes:** the machine logs in by itself, has passwordless sudo and runs an agent with
  full access, so reaching bombadil-remote is root on the machine.
- **Tailnet only:** never Funnel. `bombadil remote doctor` fails if `tailscale serve status`
  shows any funnel.
- **Localhost only:** bombadil-remote, zellij web and wayvnc listen on 127.0.0.1.
- **Header check:** bombadil-remote answers only requests whose `Tailscale-User-Login` is the
  recorded owner, and 403s the rest, including requests with no header (tagged devices and
  Funnel never carry one).
  - A local process could forge the header, but every local process already is the owner.
- **WebSocket upgrades:** the Origin must be `https://<host>.<tailnet>.ts.net`. No tokens go in
  query strings; serve has dropped them on upgrades (tailscale #18651).
- **Tailscale 1.102 or later:** this year's releases fixed SSH vulnerabilities (TS-2026-004,
  -006, -009, -010).
- **Tailnet policy:** only the owner's own devices reach this machine.
- **Open question:** the machine has no lock screen, so whoever sits at it sees what the owner
  does remotely. Lock it (hyprlock) while away?

## Where it touches other work

Almost all of it is new: `bin/bombadil-remote`, `src/bombadil/remote/`, `share/remote/`, the
systemd units and the tests. These lines are in files other threads own. Change them after
those threads merge.

| File | Owner | Change |
|---|---|---|
| `src/bombadil/agentd.py` (`open_url`, `status`) | native login, #5 | Broadcast `open_url` so remote clients see sign-in pages; add `away` to status |
| `share/zellij/config.kdl` | coding sessions, #4 | Web sharing on, base URL `/term` |
| `iso/airootfs/etc/skel/.config/hypr/hyprland.lua` | laptop VM fixes | Create the `remote` output; the bar on every output |
| `src/bombadil/mcp_server.py` | app kit, #2, also edits it | `send_file` tool |
| `iso/packages.x86_64`, `bombadil-setup` | everyone | Packages; the "reach this machine" step |

## Build order

Each step is done when its check passes from the owner's iPhone on cellular.

1. **Reach, the pill on the phone, pushes, staying awake.**
   - `tailscale ping` says direct.
   - A prompt runs from the phone, and the push arrives with the app closed.
2. **Logins while away.**
   - `aws sso login` with an expired session finishes from the iPhone with the passkey.
   - With a live session it finishes with nobody touching anything.
3. **Terminal.** A coding session opens by name at `/term/<name>` and survives the phone
   locking.
4. **Files.** A photo shared from the phone lands in `~/Downloads`; "send me that file"
   arrives on the phone.
5. **Desktop.** `/desktop` shows the `remote` output at the phone's size while the monitor is
   off.
6. **The live tab.** A form in the browser panel is filled from the phone.

**Testable here:**
- The relay, header check, away state and replay guard, as unit tests with fake headers.
- The web app, with Playwright against a fake agentd.
- The `remote` output and wayvnc, in the VM smoke test.

**Needs the owner:** Tailscale itself, which needs a tailnet, and anything on the iPhone.

## Not verified yet

- The exact `hyprctl` call that sets a headless output's mode under the Lua config.
- wayvnc resizing Hyprland 0.56's headless output when noVNC asks. Fallback: bombadil-remote
  sets the mode itself.
- Whether Chromium keeps sending screencast frames while the browser panel's special workspace
  is hidden. If not: `--disable-renderer-backgrounding` and
  `--disable-backgrounding-occluded-windows` (in `browser.py`, owned by #5).
- noVNC through `tailscale serve` end to end.
- Web Push to an iPhone from a tailnet-only origin (see piece 2).
- zellij web's exact option names, and whether its token login survives the iPhone's
  Home-Screen app storage.

## Sources

- AWS CLI SSO: https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html,
  https://docs.aws.amazon.com/cli/latest/reference/sso/login.html,
  https://aws.amazon.com/blogs/developer/aws-cli-adds-pkce-based-authorization-for-sso/,
  https://github.com/aws/aws-cli/issues/9098,
  https://docs.aws.amazon.com/singlesignon/latest/userguide/user-session-duration-prereqs-considerations.html
- Claude Code and Codex logins: https://code.claude.com/docs/en/authentication,
  https://code.claude.com/docs/en/remote-control, https://learn.chatgpt.com/docs/auth
- CDP: https://github.com/ChromeDevTools/devtools-protocol,
  https://developer.chrome.com/blog/remote-debugging-port
- Passkeys over hybrid transport: https://www.corbado.com/blog/webauthn-passkey-qr-code
- Tailscale: https://tailscale.com/kb/1312/serve, https://tailscale.com/docs/features/tailscale-ssh,
  https://tailscale.com/docs/features/taildrop.md,
  https://tailscale.com/docs/reference/connection-types,
  https://tailscale.com/docs/features/client/ios-vpn-on-demand,
  https://tailscale.com/security-bulletins, https://github.com/tailscale/tailscale/issues/18651
- zellij web: https://zellij.dev/documentation/web-client.html, https://zellij.dev/news/
- Web Push on iOS: https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/,
  https://github.com/AltanS/collie; ntfy: https://docs.ntfy.sh/config/
- Hyprland and wayvnc: https://wiki.hypr.land, https://github.com/any1/wayvnc/issues/452,
  https://github.com/hyprwm/xdg-desktop-portal-hyprland/issues/437,
  https://github.com/MuNeNICK/hypr-rdp
- Sunshine: https://github.com/LizardByte/Sunshine/issues/5803,
  https://www.smolkin.org/blog/2026/05/how-to-fix-sunshine-moonlight-rtsp-error-60-tailscale.html,
  https://cfreeman.cloud/breaking-the-5-mbps-barrier-streaming-moonlight-over-tailscale-with-full-bandwidth/
- Staying awake: https://man.archlinux.org/man/logind.conf.5,
  https://man.archlinux.org/man/systemd-inhibit.1
- Files: https://hacdias.com/2026/07/28/filebrowser/, https://copyparty.eu/cli/

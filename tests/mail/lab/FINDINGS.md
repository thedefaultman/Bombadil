# Bombadil Mail lab: what a real Thunderbird 157 does

Evidence for the Mail feature: Thunderbird running unseen, a Bombadil MailExtension inside it, native
messaging to a host script, and a Unix socket to the service (`docs/MAIL.md`). Everything below was
measured on **Mozilla's release tarball Thunderbird 157.0** (build 20260928193122), with 156.0 and ESR
140.17.0 as cross-checks for Q1, on Linux x86_64 under Xvfb (plus openbox where a window manager
mattered) and with `--headless`. The mail server is a local Dovecot 2.3.21 plus an SMTP sink
(`mailserver.py`). The raw outputs go to `~/.cache/bombadil-lab/results/` (not in the repo); every number
comes from a script in this directory, so anyone can rerun it.

Honest limits:

- It is Mozilla's tarball, not the Arch `thunderbird` package. Arch's PKGBUILD and mozconfig were read
  (Q1) but that binary was not run.
- Xvfb and openbox stand in for Hyprland. A "hidden workspace" is simulated by unmapped or minimised X
  windows. No Wayland run.
- No real Gmail, Outlook or iCloud account was used. Their settings come from the Thunderbird source in
  `omni.ja` and the live ISP database.
- Longest runs: 10 minutes idle (Q2), about 2 minutes for the footprint (Q3). Days of uptime are untested.

## Summary

| Q | Question | Answer |
|---|---|---|
| Q1 | Does an unsigned MailExtension load in release TB 157 without a click? | Yes, four ways. Best: an enterprise policy `ExtensionSettings` `force_installed` with a `file://` URL (no prefs, upgrades handled). Also `distribution/extensions/<id>.xpi` (no prefs, once per profile), a profile `extensions/<id>.xpi` plus `extensions.autoDisableScopes=0`, and Marionette `Addon:Install`. Same on 156 and ESR 140. |
| Q2 | Native messaging | Works. Manifest dirs: only `~/.mozilla/native-messaging-hosts` and `/usr/lib/mozilla/native-messaging-hosts`. Host to add-on frame max 1 MiB; add-on to host 64 MB is fine. An open port keeps a persistent MV2, an MV2 event page and an MV3 background alive; 10 minutes idle, same host pid, round trips 3 to 9 ms. A 20 MB attachment chunked in 41 frames arrives hash-identical. |
| Q4 | API fit | A good fit with a short gap list. Everything verified against the IMAP and SMTP server. INBOX new-mail event 0.3 to 1.0 s. |
| Q3 | Unseen | `--headless` works with no display. Idle PSS 384 MB headless, 427 MB windowed, 0.0% CPU. Cold start to add-on connected 1.0 to 1.4 s. Unmapped or minimised windows change nothing. |
| Q5 | Reply while hidden | `compose.beginReply` plus `sendMessage` work with the window minimised, unmapped or unfocused (0.14 to 0.29 s). Modal dialogs (over 20 MiB, SMTP refused, wrong password) block the call forever and open a separate window. |

## Q1 Signing

Source evidence (`omni.ja` extracted to `~/.cache/bombadil-lab/omni-157`):

- `modules/AppConstants.sys.mjs`: `MOZ_REQUIRE_SIGNING: false`, `MOZ_UNSIGNED_APP_SCOPE: false`,
  `MOZ_ALLOW_ADDON_SIDELOAD: false`.
- `greprefs.js:1162`: `pref("xpinstall.signatures.required", false)`; `XPIProvider.sys.mjs:73` says "only
  supported in dev builds" (honoured here because the build flag is off: control d1).
- `defaults/pref/all-thunderbird.js`: `extensions.autoDisableScopes=15`, `extensions.startupScanScopes=4`,
  `extensions.ui.disableUnsignedWarnings=true`.

Matrix (`q1_signing_matrix.py`, a fresh profile per row; "loaded" means the add-on ran and its native
messaging hello reached the host; state read from `extensions.json`):

| # | How the unsigned XPI gets in | 157.0 | 156.0 | ESR 140.17 | Notes |
|---|---|---|---|---|---|
| a1 | `<app>/distribution/policies.json` ExtensionSettings `force_installed`, `install_url: file://...xpi` | LOADED | LOADED | LOADED | no prefs; signedState 0, location app-profile |
| a2 | `/etc/thunderbird/policies/policies.json`, same | LOADED | - | - | needs root to write |
| b1 | `<profile>/extensions/<id>.xpi` + `extensions.autoDisableScopes=0` | LOADED | LOADED | LOADED | the pref is the trick |
| b2 | unpacked dir `<profile>/extensions/<id>/`, same prefs | LOADED | - | - | signedState -1 |
| b3 | b1 without the pref (default 15) | not loaded | not loaded | not loaded | installed but `userDisabled: true` |
| c1 | `<app>/distribution/extensions/<id>.xpi`, no prefs | LOADED | LOADED | LOADED | copied into the profile at first run |
| c2 | c1 + `autoDisableScopes=0` | LOADED | - | - | |
| e1 | `<app>/extensions/<id>.xpi` | not loaded (not listed) | same | listed app-global, userDisabled | do not use |
| e2 | `<app>/features/<id>.xpi` | not loaded | - | - | do not use |
| d1 | b1 with `xpinstall.signatures.required=true` in user.js | not loaded | not loaded | not loaded | control: the pref is honoured |
| d2 | d1 + a policy `Preferences` entry setting it false | not loaded | not loaded | not loaded | "Preference not allowed for stability reasons." |
| d3 | d1 + AutoConfig `lockPref("xpinstall.signatures.required", false)` | LOADED | LOADED | LOADED | `defaults/pref/autoconfig.js` + a `.cfg` in the app dir |
| d4 | policy `force_installed` while signatures are required | not loaded | - | - | `Download failed - ERROR_SIGNEDSTATE_REQUIRED` |

Marionette fallback (`q1_marionette.py`): `thunderbird --marionette` opens TCP 2828 on the release build.
`WebDriver:NewSession`, then `Addon:Install {path, temporary: true|false}` loaded the unsigned XPI in 0.09
to 0.11 s and the add-on reached its native host; both modes worked. `Marionette:SetContext chrome` is
refused without `-remote-allow-system-access`, but `Addon:Install` does not need it. A temporary install
does not survive a restart. It is a fallback only: it opens a debugging port.

Upgrades (read from `PoliciesHelpers.sys.mjs` and `XPIProvider.sys.mjs`, not exercised by a version bump):

- Policy: each start re-reads the `file://` source, cancels on an equal or older version, installs a newer
  one. Replacing the XPI and restarting is enough.
- `distribution/extensions`: installed once per profile (`extensions.installedDistroAddon.<id>`), again
  only when the application build changes. A new add-on version without a new Thunderbird build is not
  picked up unless that pref is cleared.
- Profile XPI: a replaced file is only seen if `extensions.startupScanScopes` includes scope 1 (the lab
  sets 15). **Production sets `extensions.startupScanScopes=15` along with `autoDisableScopes=0`.**

Arch package (PKGBUILD, mozconfig and vendor prefs from Arch's GitLab, pkgver 156.0.1 at the time): no
`--enable-require-addon-signing`; it adds `--with-unsigned-addon-scopes=app,system` and
`--allow-addon-sideload`; its vendor prefs set `extensions.autoDisableScopes=11` (profile, user and system
scopes disabled; app scope 4 exempt), `mail.shell.checkDefaultMail=false` and telemetry upload off;
`MOZ_APP_REMOTINGNAME=org.mozilla.Thunderbird`, so the Wayland app id is `org.mozilla.Thunderbird` and the
`(?i)^(.*\.)?thunderbird.*$` rule in `docs/MAIL.md` matches it. Inference, not run: a1 and c1 need no pref
so the 11 does not matter; b1 would still need our `autoDisableScopes=0`.

What Bombadil does with this: the service installs the add-on through the profile (b1) so a checkout works
with no root and no packaging; an image may also ship the policy route:

    {"policies": {"ExtensionSettings": {"<addon id>": {"installation_mode": "force_installed",
                  "install_url": "file:///usr/share/bombadil/mail/bombadil-mail.xpi"}}}}

in `/etc/thunderbird/policies/policies.json` (the tested form is `POLICY_EXT_ONLY` in
`q1_signing_matrix.py`). `force_installed` also makes the add-on non-removable in the Add-ons Manager.
Unlisted AMO signing was not tested (it needs an AMO account) and is not needed. If a build ever sets
`MOZ_REQUIRE_SIGNING`, the pref route stops working (dev builds only) and only a Mozilla-signed XPI would
load; the matrix script reruns in one command.

## Q2 Native messaging

The lab add-on `extension/` (MV2, persistent, `nativeMessaging`) and `host/bombadil_mail_host.py` (a stdio
to Unix socket relay that logs every start, signal, EOF and frame). Script `q2_native_messaging.py`.

| Check | Result |
|---|---|
| Manifest search dirs | `~/.mozilla/native-messaging-hosts/bombadil_mail.json` and `/usr/lib/mozilla/native-messaging-hosts/` are found. Not searched: `~/.thunderbird/`, `~/.config/mozilla/`, `/usr/lib64/mozilla/`, `/usr/lib/thunderbird/`, `/usr/share/mozilla/`, `/etc/mozilla/`, `/etc/thunderbird/` ("No such native application bombadil_mail"). |
| Permission | Without `nativeMessaging`: `TypeError: messenger.runtime.connectNative is not a function`. No prompt or window when the add-on is installed by any Q1 path. |
| Manifest checks | `allowed_extensions` not listing the id: "No such native application bombadil_mail". Host path missing: "An unexpected error occurred". |
| Lifetime, 100 s idle each | MV2 persistent, MV2 `persistent: false` event page and MV3 event page: the same page object, the same host pid, no exit logged, `connectNative` called once. `ext-backgroundPage.js` does not suspend a background page with an open native port (the default idle timeout is 30 s, `extensions.background.idle.timeout`). |
| 10 minutes idle (persistent MV2) | host pid unchanged, same page, ping round trip 3 to 9 ms and a 200 KB add-on to host frame ok every minute. RSS sum flat at 588 to 598 MB. |
| Host killed (SIGKILL) | the add-on saw the port disconnect and reconnected 2.4 s later (its own 2 s retry) to a new host process. |
| Thunderbird quits | the host gets stdin EOF and exits. |
| Host to add-on size | 100,000 / 900,000 / 1,048,576 bytes delivered. 1,048,577 and 2,000,000: Thunderbird closes the port ("tried to send a message of 1048577 bytes, which exceeds the limit of 1048576 bytes") and the add-on must reconnect. The limit is per frame. The 384 KiB blob pieces (512 KiB of base64) fit. |
| Add-on to host size | 1 / 4 / 16 / 64 MB ok (0.0 / 0.2 / 2.1 / 32.8 s, superlinear: building the JSON string in the add-on). 200 MB failed inside the add-on ("allocation size overflow"), not in native messaging. Practical: keep frames under about 16 MB. |
| 20 MB through chunks | host to add-on to SMTP: 41 chunks of 511 KiB raw (699 KB base64 frames): upload 2.1 s, `messages.sendMessage` 3.1 s, sha256 of the delivered attachment identical. Add-on to host: a received 20 MB attachment as one 28 MB frame in 9.8 s, identical. |

Gotchas: the raw chunk size must be a multiple of 3 bytes or the joined base64 is invalid;
`messages.sendMessage` hangs on a message over 20 MiB until a dialog is clicked (Q5).

## Q4 API fit

`q4_api_fit.py` (26 checks, each verified on the server, not just in the API reply) and
`q4_other_folders.py`.

| Need | Verdict | Evidence or gap |
|---|---|---|
| Accounts, identities, Local Folders | ok | `accounts.list`: `account1` (imap), `account2` (none = Local Folders); identity "Lab Tester <test@example.test>". Needs `accountsRead`, `accountsIdentities`. |
| Folders by special use | ok | `folders.query({specialUse})` found inbox, sent, drafts, archives, trash, junk. Names are localised: Junk shows as "Spam", Archive as "Archives"; paths are the server's. |
| List, paginate | ok | 130 extra mails synced in 1.1 s; page 1 = 100, page 2 via `messageListId`; total 137. |
| Query | ok with gaps | by folder, unread, flagged, attachment, author, recipients, subject, full text, body, fromDate. No `fromMail`: use `author`. `fromDate` and `toDate` must be Date objects: an ISO string or a number never answers (no reply, no error). |
| Read content | ok | `getFull` gives text/plain and text/html parts; an HTML-only mail has only text/html. Unicode is correct. |
| Raw and headers | partial | `getRaw` is prefixed with `X-Mozilla-Status:` lines. `MessageHeader.subject` has "Re: " stripped. |
| Attachments | ok | `listAttachments` plus `getAttachmentFile`; a PDF's sha256 equals the source. |
| Mark read, flagged | ok | server FLAGS `(\Flagged \Seen)`. |
| Move, copy | ok | verified on the server. |
| Archive | partial | `messages.archive` leaves INBOX and files into `Archive/<year>`. `mail.identity.id1.archive_granularity=0` makes it flat. |
| Delete | ok | to Trash; `deletePermanently` sets `\Deleted` (expunged at compaction). |
| New mail event | ok for INBOX | `messages.onNewMailReceived` 0.3 to 1.0 s (IDLE), arguments `[MailFolder, MessageList]`. |
| Contacts | ok | `addressBooks.list` (Personal, Collected), `contacts.quickSearch`; a sent-to address is collected; `contacts.create` uses PrimaryEmail, FirstName, LastName. |
| Send, no window | ok | `messages.sendMessage` in the background: no window, 0.08 s, the SMTP sink got it with its attachment, a Sent copy on the server. The permission `messages.send` is optional-only (the lab pre-seeds `extension-preferences.json`; production needs a seeded grant or a user gesture). |
| Send, threading | gap | `messages.sendMessage` drops `In-Reply-To` and `References` (only `X-*` and `List-*` custom headers survive). Use `compose.beginReply`. |
| Compose new | ok | `compose.beginNew` opens a window (class Msgcompose/thunderbird); `compose.sendMessage` 2.19 s; the window closes. Needs `compose` and `compose.send`. |
| Reply | ok | `compose.beginReply(id, "replyToSender")`: In-Reply-To `<plan-2@...>`, References `<plan-1@...> <plan-2@...>`, "Re: Project plan", quoted body, Sent copy. |
| Drafts | ok | `messages.saveMessage`: one draft on the server. |
| Stable ids | gap | `MessageHeader.id` is session-local: after a restart only 2 of 15 stayed equal. Key on `headerMessageId` plus the folder path. |

New mail outside the Inbox (`q4_other_folders.py`: mail delivered at once to INBOX, Archive, Junk and Sent;
a 100 s watch; check_time 1 min):

| Folder | `onNewMailReceived` | local count, query sees it | `folders.onFolderInfoChanged` |
|---|---|---|---|
| INBOX | 0.3 to 0.4 s | 2.1 s | 0.3 to 0.4 s |
| Archive | never | about 10.2 s | about 9.2 s |
| Sent | never | about 10.2 s | about 8.8 s |
| Junk | never | never in 100 s (330 s in an earlier run) | none |

`mail.server.server1.check_all_folders_for_new=true` changed nothing. With `autosync_offline_stores=false`
and `offline_download=false`, Archive and Sent were not noticed at all in 100 s: the 10 s catch-up is IMAP
autosync (Thunderbird's default for IMAP) and it skips Junk. INBOX is event-driven; other folders arrive
through autosync and `folders.onFolderInfoChanged`. `messages.query({online: true})` did not find them
either.

## Q3 Unseen

`q3_unseen.py` (headless, hidden, footprint, firstrun, compose, modal, authfail).

| Check | Result |
|---|---|
| `--headless`, DISPLAY unset | the add-on connects (2.6 s after launch in that run, 1.0 s in the footprint runs); IMAP synced (7 of 7); new-mail event 0.70 s; `messages.sendMessage` and `beginReply` + `sendMessage` deliver; alive 20 s later. `windows.getAll` reports one virtual normal, maximized window. |
| Window unmapped (xdotool windowunmap) | the API answers, new-mail event 0.73 s, background send ok. |
| Window minimised (openbox, `_NET_WM_STATE_HIDDEN`) | new-mail event 0.73 s, API ok. |
| Cold start (fresh profile, warm file cache, 3 runs) | windowed 1.26 to 1.28 s; unmapped 1.35 to 1.38 s; headless 0.98 to 1.05 s, from launch to the add-on's first native frame. Restart on an existing profile (stop and start): 5.25 s. On a 4-core VM; a cold disk or a big mailbox is slower. |
| Footprint at 2 min idle (7 mails) | 3 processes (main plus 2 helpers). RSS sum and PSS sum: windowed 584 and 427 MB; windowed then unmapped 576 and 420 MB; headless 515 and 384 MB. CPU 0.0% of a core (20 s average at t = 2 min). A real mailbox adds to this. |
| Windows with the lab prefs | two X windows: the main window and a permanent 10x10 helper window at +10+10 (instance thunderbird, class Thunderbird). Classes (instance, class): main Mail / thunderbird, compose Msgcompose / thunderbird, dialogs Thunderbird / thunderbird. |

First-run sources (`q3_unseen.py firstrun`, one pref ablated at a time):

| Without this pref | What appears |
|---|---|
| `mail.shell.checkDefaultClient=false` | a separate X window "System Integration" (440x252): the only first-run popup that is its own window |
| `mail.provider.suppress_dialog_on_startup=true` | with no account: the Account Hub ("Add your email address") in the main window, and Thunderbird creates Local Folders itself; with an account, no visible difference |
| `mailnews.start_page.enabled=false` | an in-window start page (live.thunderbird.net) with a certificate warning when there is no route |
| `mail.rights.version=1` | the "Know your rights" bar (in-window) |
| no account and no quiet prefs | an extra 10x10 window at -100,-100, the Home tab and the Account Hub |

With the full quiet set and an account, a start shows only the Inbox window. A policies.json `Preferences`
entry accepts `mail.*`, `mailnews.*`, `extensions.*`, `datareporting.policy.*`, `network.*`, `app.update.*`
and others, and there are `DisableTelemetry`, `DisableAppUpdate` and `DontCheckDefaultClient` policies;
they were not tested as a replacement for user.js.

## Q5 Reply with the window hidden

`q3_unseen.py compose`: for each state, `compose.beginReply(plan-2)` then `compose.sendMessage`; the SMTP
sink saw `In-Reply-To: <plan-2@example.org>` every time.

| Compose window state at send | beginReply | sendMessage |
|---|---|---|
| normal, focused | 0.44 s | 0.20 s |
| minimised via `windows.update` | 0.36 s | 0.17 s |
| minimised by the window manager | 0.34 s | 0.22 s |
| unmapped (xdotool windowunmap) | 0.38 s | 0.27 s |
| not focused (focus on the main window) | 0.37 s | 0.14 s |
| `windows.update(focused: false)` | 0.19 s | 0.26 s |
| beginNew, unmap, send | | delivered |
| `--headless` | 0.36 s | 0.29 s |

A hidden or unfocused compose window neither delays nor blocks sending; it is a normal window of class
thunderbird, so the class rule in `docs/MAIL.md` catches it. Not tested: a real Hyprland special workspace.

Blocking dialogs are the real risk (`q3_unseen.py modal` and `authfail`). `Services.prompt` is modal and
the API call never settles until it is answered. Each opens a top-level window of class thunderbird (the
Hyprland rule would put it on the hidden workspace where nobody can click it):

| Trigger | Window | API behaviour |
|---|---|---|
| A message larger than `mailnews.message_warning_size` (default 20,971,520 bytes, about a 15 MiB attachment after base64), `MessageSend.sys.mjs` | "Confirm" 676x114 | `messages.sendMessage` pending (150 s watched); Return sends it. A 12 MiB attachment sent in 3 s with no dialog. With `mailnews.message_warning_size=0`, 15 MiB sent with no dialog. |
| SMTP connection refused | "Send Message Error" 676x132 | pending until dismissed, then rejects with `messages.sendMessage failed: Sending FAILED!`. `compose.sendMessage` goes through the same alert (not run). |
| Wrong stored IMAP password | "Login to account ... failed" 530x114 | the API keeps answering from the local store (Inbox 0 messages); the dialog comes back after Escape within 100 s; a `messages.sendMessage` with a wrong SMTP password was still pending at 25 s (confounded: the IMAP dialog was already open). |

Consequences: set `mailnews.message_warning_size=0`; wrap every send in a timeout (the service has 60 s and
the add-on 55 s); on a timeout or a login failure, restart the engine and, when a person has to answer,
stage the engine window (`engine_window stage`). No WebExtension API dismisses these dialogs. OAuth
accounts raise the browser flow instead of a dialog (next section), untested live.

## Accounts, hosts, scopes (Gmail, Outlook and Microsoft 365, iCloud)

Source: `modules/OAuth2Providers.sys.mjs`, `OAuth2Module.sys.mjs`, `OAuth2.sys.mjs`,
`OAuth2CustomDetails.sys.mjs` (157.0 `omni.ja`) and the live ISP database
(`https://autoconfig.thunderbird.net/v1.1/<domain>`; Thunderbird's `mailnews.auto_config_url` is
`https://live.thunderbird.net/autoconfig/v1.1/`; the tarball ships no offline copy).

| | Gmail | Outlook.com and Microsoft 365 | iCloud |
|---|---|---|---|
| IMAP | `imap.gmail.com:993` SSL (socketType 3) | `outlook.office365.com:993` SSL | `imap.mail.me.com:993` SSL |
| SMTP | `smtp.gmail.com:465` SSL | `smtp-mail.outlook.com:587` STARTTLS (consumer, ISP DB) or `smtp.office365.com:587` STARTTLS (Microsoft 365); the same OAuth provider | `smtp.mail.me.com:587` STARTTLS |
| Auth | authMethod 10 (OAuth2); the ISP DB also lists password | authMethod 10 | authMethod 3, an app-specific password (Apple ID two-factor); no OAuth in Thunderbird |
| IMAP user name | the full address | the full address | the local part (`%EMAILLOCALPART%`); the SMTP user name is the full address |
| OAuth issuer (`oauth://<issuer>` is the login manager origin) | `accounts.google.com` | `login.microsoftonline.com` | none |
| Client registration | Thunderbird's own, in `OAuth2Providers.sys.mjs` | Thunderbird's own (no secret) | none |
| Scopes (IMAP and SMTP) | `https://mail.google.com/` (the token request also asks for the CardDAV and calendar scopes) | `https://outlook.office.com/IMAP.AccessAsUser.All`, `https://outlook.office.com/SMTP.Send`, `offline_access` | none |
| Endpoints | auth `https://accounts.google.com/o/oauth2/auth`, token `https://www.googleapis.com/oauth2/v3/token`, PKCE on | auth `https://login.microsoftonline.com/common/oauth2/v2.0/authorize`, token `.../common/oauth2/v2.0/token` | none |
| Browser | external (`mailnews.oauth.useExternalBrowser=true`, the default), loopback redirect `http://127.0.0.1:<port>` | external | none |
| Token storage | login manager: origin `oauth://accounts.google.com`, httpRealm the scope string, user name the address, password the refresh token | the same with `oauth://login.microsoftonline.com` | password logins for `imap://imap.mail.me.com` and `smtp://smtp.mail.me.com` |

Notes:

- The client registrations are in the Thunderbird source, so any unpatched build (Arch's included) uses
  Mozilla's Google and Microsoft registrations; Bombadil needs none of its own. A Microsoft 365 tenant that
  blocks third-party apps refuses consent ("needs admin approval"): the `blocked` state in `docs/MAIL.md`.
- Per-server override prefs exist (`mail.server.serverN.oauth2.*`, `mail.smtpserver.smtpN.oauth2.*`) and
  are honoured only when `experimental.mail.ews.overrideOAuth.enabled=true`. Extensions can register
  providers (the `oauth_provider` manifest key).
- Flow prefs: `mailnews.oauth.useExternalBrowser=true`, `mailnews.oauth.useSchemeRedirect=true` (only
  providers with a scheme redirect, i.e. Yahoo), `mailnews.oauth.usePrivateBrowser=false`. Setting
  `useExternalBrowser=false` gives an in-app window that `engine_window stage` could surface.
- `seed_logins.py` can pre-seed a login through NSS (`logins.json` and `key4.db`), but an OAuth refresh
  token needs a real consent, so the lab cannot fake it. `LAB_KIND=gmail|outlook|icloud` builds those
  accounts' prefs (authMethod 10 and no stored password for the OAuth two); they cannot connect.
- A password account (iCloud, generic IMAP) raises Thunderbird's own password prompt, a modal window of
  class thunderbird: it has to be staged for the person to type into, and Bombadil never holds the password.
- ISP lookups: `fetchFromISP` sends the address to the provider and `mailnews.auto_config.guess` probes
  hostnames; the `mail.server.server1.*` prefs can be written directly (appendix).

## What could not be tested, and why

- Arch's binary and Hyprland or Wayland (not available in the lab).
- Real OAuth (Gmail, Microsoft) and iCloud logins (no credentials), hence also IDLE against real providers.
- A policy or distribution version bump (read in the source only).
- Unlisted AMO signing; AutoConfig on a build that really requires signing.
- Uptime beyond 10 minutes, memory growth with a large mailbox, suspend and resume.
- Catch-up sync timing for mail delivered while Thunderbird was down.

## Reproduce

    tests/mail/lab/fetch-thunderbird.sh                # prints the binary; ~/.cache/bombadil-lab/thunderbird-157.0
    tests/mail/lab/run-thunderbird.sh start            # Xvfb + Dovecot + SMTP sink + fresh profile + Thunderbird + add-on
    tests/mail/lab/run-thunderbird.sh status | shot /tmp/x.png | restart | stop | clean
    python3 tests/mail/lab/q1_signing_matrix.py --json /tmp/q1.json     # TB_VERSION=156.0 / 140.17.0esr for the cross-checks
    python3 tests/mail/lab/q1_marionette.py [--permanent]
    python3 tests/mail/lab/q2_native_messaging.py dirs|perm|lifetime|kill|sizes|chunks|idle --idle-minutes 10
    python3 tests/mail/lab/q4_api_fit.py --start --json /tmp/q4.json
    python3 tests/mail/lab/q4_other_folders.py
    python3 tests/mail/lab/q3_unseen.py headless|hidden|compose|modal|authfail|firstrun|footprint
    LAB_HEADLESS=1 LAB_NO_DISPLAY=1 tests/mail/lab/run-thunderbird.sh start        # unseen, no display
    DISPLAY=:0 LAB_XVFB=0 tests/mail/lab/run-thunderbird.sh start                   # an existing display
    BOMBADIL_LAB_DIR=/tmp/lab2 LAB_IMAP_PORT=2143 LAB_SMTP_PORT=2025 tests/mail/lab/run-thunderbird.sh start   # a second lab

The mail server's user is `test@example.test`, password `lab`, IMAP `127.0.0.1:1143`, SMTP `127.0.0.1:1025`;
folders INBOX, Sent, Drafts, Trash, Archive (plus Junk and Templates); seeds are `seed/*.eml` (a
sub-directory is a folder; `*.seen.eml` and `*.flagged.eml` set flags); mail the SMTP sink accepts is
recorded under `<lab>/mail/smtp/NNNN.eml` and `sent.json` and delivered to INBOX when addressed to the test
user. The lab is only created under /tmp, /var/tmp or `~/.cache` (`safe_lab` refuses other paths);
downloads and results live in `~/.cache/bombadil-lab`.

## Files in this directory

| File | Purpose |
|---|---|
| `fetch-thunderbird.sh` | an idempotent download and unpack of a release tarball (TB_VERSION, TB_LOCALE, TB_URL) |
| `run-thunderbird.sh` | start, stop, clean, restart, status, shot, env; every knob is documented in its header |
| `mailserver.py` | the Dovecot config, the SMTP sink, seeding, a CLI |
| `make_profile.py`, `seed_logins.py`, `print_prefs.py` | the generated profile (prefs tables with reasons, accounts, NSS-encrypted logins, add-on install, permissions); `print_prefs.py` renders the tables |
| `extension/`, `host/bombadil_mail_host.py`, `build_xpi.py`, `lab_bridge.py`, `labtest.py` | the lab add-on (a generic `messenger.*` RPC), the native host, the XPI builder, the test harness |
| `q1_signing_matrix.py`, `q1_marionette.py`, `q2_native_messaging.py`, `q3_unseen.py`, `q4_api_fit.py`, `q4_other_folders.py` | one script per question |
| `seed/` | seed mails |

## Appendix: every pref the lab writes

Generated from `make_profile.py`, the single source (the reasons are in the code): run
`python3 tests/mail/lab/print_prefs.py` for the tables. `LAB_NO_QUIET=1` omits the quiet table,
`LAB_ABLATE=a,b` omits single prefs, `LAB_EXTRA_PREFS=file.js` appends. The production profile writer
(`src/bombadil/mail/engine.py`) carries the same prefs, each with its reason.

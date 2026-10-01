"""Thunderbird as a child of the mail service: its profile, its add-on, its accounts, its window.

The service owns the idea ("keep Thunderbird running while there is an account"); this module owns the program.
`ThunderbirdProcess` has the interface `fake.FakeProcess` has, and the service calls it from one worker thread
of its own, so every method returns in bounded time (the longest is `stop`: ten seconds of asking, then a kill).

What it writes, and why it is shaped this way:

- **user.js is the one place Bombadil's settings live.** Thunderbird reads `user.js` at every start and folds
  it into `prefs.js`, which it rewrites at exit, so a pref written to `prefs.js` while Thunderbird runs is
  lost and one written to `user.js` is not. `user.js` is therefore generated whole, from the quiet prefs below
  and from `bombadil-engine.json`, a small registry (also in the profile) of the accounts that were seeded.
  Seeding or forgetting an account changes the registry and renders `user.js` again; nothing parses `user.js`.
- **An account is written the way Thunderbird's own setup writes it**, from the address and the provider
  (`accounts.Provider`): an IMAP server, an SMTP server and an identity. Bombadil never holds a password:
  OAuth accounts sign in on the provider's page and the others get Thunderbird's own password prompt, whose
  answer Thunderbird keeps in its login store. Both are the person's alone, in a window `stage` shows.
- **Forgetting is for good**, with one limit (see `forget_account`). Because `user.js` lists the accounts
  (`mail.accountmanager.accounts`), an account that is not in the registry is simply not loaded, however much of
  it `prefs.js` still has. What `prefs.js`, `logins.json`, the mail folder and Thunderbird's caches that name the
  account (its global index, folder tree and folder cache) still hold for it is removed too, but only while
  Thunderbird is not running (it would write it all back at exit), so a forget while it runs is finished by
  `stop` or the next start. Nothing here opens a mail or reads Thunderbird's mail files: only prefs,
  logins.json, lock files, and whole folders and files chosen by name.
- **The add-on is a reproducible `.xpi`** in the profile (`extensions/<id>.xpi`), built from the package's
  `share/mail/extension/` with fixed timestamps and no compression, so the same source is the same bytes on
  any machine, and rebuilt only when the source changed (its digest is the zip's comment).
- **The native-messaging manifest** is in `~/.mozilla/native-messaging-hosts`, the only place Thunderbird
  looks besides `/usr/lib/mozilla`, and names `bin/bombadil-mail-host` by absolute path.

What a real Thunderbird does that shaped the rest (measured on 157, 156 and 140 ESR; tests/mail/lab):

- Its stale-lock handling is its own: `.parentlock` is held with `fcntl`, which the kernel drops when the
  process dies however it dies, and `lock` is a symlink "ip:+pid" that a crash leaves behind and that a start
  replaces once it holds the fcntl lock. A start after a `kill -9` needs nothing from us.
- A second Thunderbird on a profile that is in use prints "already running, but is not responding" (and in a
  window, a dialog) and exits 0. So `start` first asks whether the profile's lock names a live Thunderbird,
  and `running()` is true for one that an earlier service run left behind: its add-on reconnects to the new
  service by itself (the host exits when the socket goes, Thunderbird starts a new one 2.4 s later).
- `thunderbird` is a shell script that `exec`s `thunderbird-bin`, so the child's pid is the lock's pid and the
  process group (it starts a session of its own) holds its content, GPU and host processes; killing the group
  is how nothing is left behind.
- Headless (`--headless`, no display variables) syncs, gets new-mail events and sends; its stderr fills with
  GTK warnings about a screen it does not have, hence the capped log.
"""

import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import threading
import time
import zipfile
from itertools import pairwise
from pathlib import Path

from .. import hypr, paths
from ..appkit.placement import lua_str
from . import accounts as accts
from .protocol import log, valid_email

ADDON_ID = "bombadil-mail@bombadil.local"
HOST_NAME = "bombadil_mail"
ENGINE_WORKSPACE = "special:mail-engine"

STOP_S = 10.0              # how long Thunderbird is given to quit after SIGTERM before its group is killed
WINDOW_WAIT_S = 6.0        # how long after a start a window may take to appear when someone wants to see it
# `stage` is called with a budget of its own: the service gives a call that only asks Thunderbird something
# `PROBE_S` (5 s), and a call that runs over it is a Thunderbird whose controls "are stuck", which refuses every
# other call until it returns. So one call waits for a window at most STAGE_WAIT_S, asks the compositor for at most
# HYPR_S each time, and gives a Thunderbird that has to be started again with a window STAGE_QUIT_S to quit; a
# window that is not there by then is a False, and the person's next click finds it.
STAGE_WAIT_S = 2.5
STAGE_QUIT_S = 2.0
HYPR_S = 1.5
LOG_CAP = 1 << 20          # bytes of Thunderbird's own output kept in one file; one older file is kept
REGISTRY = "bombadil-engine.json"
XPI_COMMENT = b"bombadil-source:"
ZIP_TIME = (1980, 1, 1, 0, 0, 0)
MAX_FORGOTTEN = 64         # leftovers of removed accounts waiting for Thunderbird to be stopped
SYNC_DAYS = 180            # how far back Thunderbird keeps whole mails on this machine (headers: all of them)

# Thunderbird's own class names for its windows, matched the way hyprland.lua's "mail-engine" rule does.
_WINDOW_CLASS = re.compile(r"(?i)^(.*\.)?thunderbird.*$")
_ADDRESS = re.compile(r"0x[0-9a-fA-F]{1,16}")
_MAIN_TITLE = re.compile(r"Mozilla Thunderbird\s*$")
_COMPOSE_TITLE = re.compile(r"Write: ")     # a compose window's title in Thunderbird's (en-US) strings
_ACCOUNT_ID = re.compile(r"a([0-9]{1,9})")
_HOSTNAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")
_LOOPBACK = ("127.0.0.1", "::1", "localhost")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029\ud800-\udfff]")
_LOCK = re.compile(r".*:\+([0-9]+)")
_MAIL_DIR = re.compile(r"[A-Za-z0-9.-]+-[0-9]+")
# Files in the profile that Thunderbird makes again when they are missing and that hold an account's address,
# its folders' names or what it fetched: Gloda's database (and the files SQLite keeps beside it), the folder
# tree and the folder cache.
_CACHES = ("global-messages-db.sqlite", "global-messages-db.sqlite-wal", "global-messages-db.sqlite-shm",
           "global-messages-db.sqlite-journal", "folderTree.json", "folderCache.json")
# Thunderbird's login store keys an OAuth sign-in by the provider's page, not by the account.
_ISSUER = {"google": "accounts.google.com", "microsoft": "login.microsoftonline.com"}

# The environment a Thunderbird gets: the service's own, less what would change how it behaves. MOZ_* are the
# person's debugging switches (MOZ_LOG can write mail text to a file); the other two are single-use tokens of
# whatever launched the service. BOMBADIL_* stay: the host Thunderbird starts finds mail.sock by them.
_DROP_ENV = ("DESKTOP_STARTUP_ID", "XDG_ACTIVATION_TOKEN")

# What Thunderbird would do on a first run, or on any run, that a person must never meet in a window nobody
# looks at. Each is a pref and, above it, the reason; they come from the lab's profile (tests/mail/lab/
# make_profile.py), where each one was needed or deliberately set, less what only the lab needed.
QUIET = (
    # With no account Thunderbird opens the Account Hub at start. With this it never does, and it also stops
    # making the Local Folders account itself, which `_local_folders` writes instead.
    ("mail.provider.suppress_dialog_on_startup", True),
    # The default-mail-client and default-calendar prompts of a first start.
    ("mail.shell.checkDefaultClient", False),
    ("mail.shell.checkDefaultCalendar", False),
    # The start page (a fetch from the network, with certificate errors behind a proxy) and the "know your
    # rights" bar, which shows while this is below 1.
    ("mailnews.start_page.enabled", False),
    ("mail.rights.version", 1),
    # Chat, and the in-app notification service that fetches banners every six hours.
    ("mail.chat.enabled", False),
    ("mail.inappnotifications.enabled", False),
    # A new mail is the shell's line above the pill, not a popup, a sound, a tray icon or a badge.
    ("mail.biff.show_alert", False),
    ("mail.biff.play_sound", False),
    ("mail.biff.use_system_alert", False),
    ("mail.biff.show_tray_icon_always", False),
    ("mail.biff.show_badge", False),
    # What a pref can stop of Thunderbird's calls to Mozilla is stopped here. Measured on 157, an empty profile and
    # every HTTP request sent to a proxy that refuses it: the region lookup (below), then 30 seconds after the
    # start an update check and a Remote Settings poll. No pref reaches those two: the update URL is read from the
    # default branch only, `app.update.disabledForTesting` counts in Mozilla's automation only, and release
    # builds ignore `services.settings.server`. A policies.json with DisableAppUpdate stops the first (a
    # distribution's build is usually made without an updater); nothing found stops the second. Updates are the
    # distribution's, and the add-on has no update address.
    ("app.update.enabled", False),
    ("app.update.auto", False),
    ("app.update.staging.enabled", False),
    ("app.update.checkInstallTime", False),
    ("extensions.update.enabled", False),
    ("extensions.update.autoUpdateDefault", False),
    # Measured, harmless: one "ExtensionBlocklist._client is undefined" line in the log at start.
    ("extensions.blocklist.enabled", False),
    ("datareporting.policy.dataSubmissionEnabled", False),
    ("datareporting.healthreport.uploadEnabled", False),
    ("toolkit.telemetry.enabled", False),
    ("toolkit.telemetry.unified", False),
    ("toolkit.telemetry.archive.enabled", False),
    ("toolkit.telemetry.server", ""),
    ("toolkit.telemetry.reportingpolicy.firstRun", False),
    ("datareporting.policy.firstRunURL", ""),
    ("browser.crashReports.unsubmittedCheck.enabled", False),
    # Thunderbird asks location.services.mozilla.com which country it is in, once, at the first start with no
    # stored region (an empty URL is no request; the update timer alone is not enough, it only repeats the
    # question later).
    ("browser.region.network.url", ""),
    ("browser.region.update.enabled", False),
    ("network.captive-portal-service.enabled", False),
    ("network.connectivity-service.enabled", False),
    ("browser.safebrowsing.malware.enabled", False),
    ("browser.safebrowsing.phishing.enabled", False),
    # Poll for new mail as well as IDLE, which covers the Inbox only.
    ("mail.server.default.check_new_mail", True),
    # A message over this many bytes (20 MiB by default: a 15 MiB attachment after encoding) makes a send ask
    # "are you sure?" in a modal dialog, and the add-on's promise never settles until someone clicks. 0 turns
    # the question off. Measured: 12 MiB goes in 3 s, 15 MiB hangs for the whole deadline without this.
    ("mailnews.message_warning_size", 0),
    # The same for what the compose window of a reply asks before it sends (MsgComposeCommands.js): an
    # "attachment reminder" confirm when the text says "attached" (aggressive by default) and the spelling
    # dialog. Neither is anyone's to answer in a window that is never seen.
    ("mail.compose.attachment_reminder", False),
    ("mail.compose.attachment_reminder_aggressive", False),
    ("mail.SpellCheckBeforeSend", False),
    ("mail.compose.warn_public_recipients.aggressive", False),
    # A compose window the add-on leaves open (a reply that failed) would save a draft to the person's Drafts
    # on the server every five minutes. Bombadil's drafts are its own.
    ("mail.compose.autosave", False),
    # A mail left in Thunderbird's Outbox (a send that a crash or a restart cut short) is sent when it goes back
    # online if this is 1, and asked about in a window nobody sees if it is 0. Only a press on Send sends, so:
    # never (2). Measured in Thunderbird's own source: 0 asks, 1 sends, 2 never sends.
    ("offline.send.unsent_messages", 2),
    # Always start online (2). 0 is "remember the last state", which is what a Thunderbird that was stopped
    # while the network looked down would come back to: offline, in a window nobody sees, fetching nothing, while
    # its add-on still says hello. 2 is Thunderbird's own default (OfflineStartup.sys.mjs: 0 remember, 1 ask,
    # 2 online, 3 offline, 4 automatic; all-thunderbird.js sets 2), kept here so that nothing changes it.
    ("offline.startup_state", 2),
    # Thunderbird's global index of mail (Gloda) is on by default: a second copy of every message's text, and of
    # the addresses and folders of an account that was removed, in global-messages-db.sqlite. Nothing of Mail uses
    # it (the add-on reads folders and headers directly), so it is never made.
    ("mailnews.database.global.indexer.enabled", False),
    # A message that asks for a receipt makes Thunderbird ask "send one?" when it is shown (2 is "ask"), and a
    # receipt is a message that is not Mail's press. Neither the per-account setting nor the global one sends.
    ("mail.server.default.mdn_report_enabled", False),
    ("mail.mdn.report.enabled", False),
    # Window titles are how `pick_window` tells a compose window (never shown) from a dialog, and they come from
    # the interface language. Thunderbird follows the system's unless told, and a language pack would change them.
    ("intl.locale.requested", "en-US"),
)

# Loading an unsigned add-on from the profile. Release builds do not require signatures, but a sideloaded
# add-on is installed disabled unless its scope is enabled.
ADDON = (
    # The default 15 starts every new add-on of every scope disabled, awaiting consent. 0 enables them.
    ("extensions.autoDisableScopes", 0),
    ("extensions.enabledScopes", 15),
    # The default 4 rescans only the application scope at each start, so a changed `.xpi` in the profile would
    # be noticed on a version change only. 15 rescans the profile too.
    ("extensions.startupScanScopes", 15),
    ("xpinstall.signatures.required", False),
)

LOCAL_FOLDERS = 1   # account1 and server1: Local Folders. Bombadil's account `aK` is account K+1.


# -- pure: windows --

def is_engine_window(client) -> bool:
    """Is this entry of Hyprland's `j/clients` one of Thunderbird's windows?"""
    if not isinstance(client, dict):
        return False
    return any(isinstance(client.get(k), str) and _WINDOW_CLASS.match(client[k])
               for k in ("class", "initialClass"))


def _speck(client: dict) -> bool:
    """A window of a few pixels: the invisible 10 by 10 one a GTK application keeps under XWayland, which has
    Thunderbird's class and a title that is not the main window's, so it would pass for a dialog."""
    size = client.get("size")
    return isinstance(size, list) and len(size) == 2 and all(isinstance(v, int) and v < 40 for v in size)


def engine_windows(clients) -> list[dict]:
    """Thunderbird's windows that are on screen somewhere (mapped, not hidden, bigger than a speck), in the
    order given."""
    return [c for c in clients if is_engine_window(c) and c.get("mapped", True) and not c.get("hidden")
            and not _speck(c)]


def compose_windows(clients) -> list[dict]:
    """Thunderbird's compose windows that are on screen somewhere. One has a Send button of Thunderbird's own,
    and the only press that sends is Mail's, so such a window is never shown on purpose."""
    return [c for c in engine_windows(clients) if _COMPOSE_TITLE.match(str(c.get("title") or ""))]


def pick_window(clients) -> dict | None:
    """The window to bring forward: what Thunderbird asks a person is asked in a dialog, so a window whose
    title is not the main window's ("... - Mozilla Thunderbird") comes first, then the one focused last. A
    compose window is never the one."""
    found = [c for c in engine_windows(clients) if not _COMPOSE_TITLE.match(str(c.get("title") or ""))]
    if not found:
        return None

    def rank(c):
        history = c.get("focusHistoryID")
        return (bool(_MAIN_TITLE.search(str(c.get("title") or ""))),
                history if isinstance(history, int) and history >= 0 else 1 << 30)

    return min(found, key=rank)


def window_selectors(client: dict) -> list[str]:
    """Hyprland's selectors for this window, best first: its address (every window of one program has the same
    pid, and a pid names whichever of them the compositor lists first, the main window and not the dialog that was
    picked), then its pid, which the launcher addresses its windows by and so is known to be understood, then its
    class (for a client with neither)."""
    out = []
    address, pid = client.get("address"), client.get("pid")
    if isinstance(address, str) and _ADDRESS.fullmatch(address):
        out.append(f"address:{address}")
    if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
        out.append(f"pid:{pid}")
    if not out:
        out.append(f"class:^({re.escape(str(client.get('class') or client.get('initialClass') or ''))})$")
    return out


def staged_monitor(monitors) -> dict | None:
    """The monitor on which the engine's special workspace is showing, if any."""
    for m in monitors:
        special = m.get("specialWorkspace") if isinstance(m, dict) else None
        if isinstance(special, dict) and special.get("name") == ENGINE_WORKSPACE:
            return m
    return None


def display_kind(env) -> str | None:
    """"wayland", "x11" or None for what a Thunderbird started with this environment could draw on. A display
    variable whose socket is gone (a compositor that restarted) is no display: Thunderbird would die at once
    on it, and a headless one at least keeps the mail coming."""
    wl = env.get("WAYLAND_DISPLAY")
    if wl:
        base = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        if (Path(wl) if os.path.isabs(wl) else Path(base) / wl).exists():
            return "wayland"
    x = env.get("DISPLAY")
    if x:
        local = re.fullmatch(r":([0-9]+)(?:\.[0-9]+)?", x)
        if local is None or Path(f"/tmp/.X11-unix/X{local.group(1)}").exists():   # "host:n" cannot be told
            return "x11"
    return None


# -- pure: prefs --

def _js(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(value)   # \" \\ and \uXXXX only, all of which Thunderbird's pref parser reads


def _text(value, limit: int = 200) -> str:
    """Words as a pref value: one line, no control characters. Mozilla's pref parser has no \\t, \\b or \\f."""
    return " ".join(_CONTROL.sub(" ", str(value or "")).split())[:limit]


def _host(value) -> str:
    """A server name that is safe to put in a pref and in a folder name: letters, digits, dots and hyphens."""
    host = str(value or "").strip().lower()
    if not host.isascii():
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as e:
            raise ValueError("That mail server's name cannot be used.") from e
    if _HOSTNAME.fullmatch(host) is None or ".." in host:
        raise ValueError("That mail server's name cannot be used.")
    return host


def _number(account_id) -> int:
    m = _ACCOUNT_ID.fullmatch(str(account_id))
    if m is None or int(m.group(1)) < 1:
        raise ValueError("That is not one of Mail's account ids.")
    return int(m.group(1)) + LOCAL_FOLDERS


def _checked(e) -> dict:
    """An account entry with every part checked and spelled one way. The registry's entries pass through here
    again when they are read back: the file is in a profile that anything running as the person can write, and
    its parts become pref names, folder names and hosts. Raises ValueError for anything that is not usable."""
    e = e if isinstance(e, dict) else {}
    email = str(e.get("email") or "").strip().lower()
    if not valid_email(email):
        raise ValueError("That is not an email address.")
    if e.get("provider") not in accts.PROVIDERS or e.get("auth") not in ("oauth2", "password"):
        raise ValueError("That provider or sign-in method is not known.")
    out = {"id": str(e.get("id")), "n": _number(e.get("id")), "email": email, "name": _text(e.get("name")),
           "sender": _text(e.get("sender")), "provider": e["provider"], "auth": e["auth"]}
    for side in ("imap", "smtp"):
        part = e.get(side) if isinstance(e.get(side), dict) else {}
        host, port, security = _host(part.get("host")), part.get("port"), part.get("security")
        if security not in ("ssl", "starttls", "none"):
            raise ValueError("That connection security is not known.")
        if security == "none" and host not in _LOOPBACK:
            raise ValueError("Mail is not sent in the clear to another computer.")   # only a server on this one
        if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
            raise ValueError("That mail server's port cannot be used.")
        out[side] = {"host": host, "port": port, "security": security}
    return out


def entry_for(account: dict, provider: accts.Provider) -> dict:
    """What the registry keeps for an account: the service's row and accounts.py's provider, checked. The
    service may hand over the generic provider as a template (`imap.{domain}`); it is made for the address."""
    provider = accts.for_domain(provider, str(account.get("email") or "").rpartition("@")[2].strip().lower())
    return _checked({"id": account.get("id"), "email": account.get("email"), "name": account.get("name"),
                     "sender": account.get("sender"), "provider": provider.key, "auth": provider.auth,
                     "imap": {"host": provider.imap_host, "port": provider.imap_port,
                              "security": provider.imap_security},
                     "smtp": {"host": provider.smtp_host, "port": provider.smtp_port,
                              "security": provider.smtp_security}})


def mail_dir(entry: dict) -> str:
    """The folder under ImapMail that holds the account's copy of its mail: the host and the account's own
    number, so two accounts at one provider do not share a folder and a name is never someone's input."""
    return f"{entry['imap']['host']}-{entry['n']}"


def account_prefs(a: dict) -> list[tuple[str, object]]:
    """The prefs of one account, as `entry_for` made it: what Thunderbird's own setup writes for an IMAP
    account, with its SMTP server and identity."""
    n = a["n"]
    account, server, ident, smtp = f"account{n}", f"server{n}", f"id{n}", f"smtp{n}"
    secure = {"ssl": 3, "starttls": 2, "none": 0}   # nsMsgSocketType
    method = 10 if a["auth"] == "oauth2" else 3       # nsMsgAuthMethod: OAuth2, or the password over TLS
    # iCloud's IMAP login is the part before the @; its SMTP login is the whole address.
    imap_user = a["email"].partition("@")[0] if a["provider"] == "icloud" else a["email"]
    imap, out = a["imap"], a["smtp"]
    s = f"mail.server.{server}."
    i = f"mail.identity.{ident}."
    t = f"mail.smtpserver.{smtp}."
    prefs = [
        (f"mail.account.{account}.server", server),
        (f"mail.account.{account}.identities", ident),
        (s + "type", "imap"),
        (s + "hostname", imap["host"]),
        (s + "port", imap["port"]),
        (s + "userName", imap_user),
        (s + "name", a["name"] or a["email"]),
        (s + "socketType", secure[imap["security"]]),
        (s + "authMethod", method),
        # Connect at start rather than when a folder is first opened: nothing opens one.
        (s + "login_at_startup", True),
        (s + "check_new_mail", True),
        (s + "check_time", 1),          # minutes; IDLE covers the Inbox between
        (s + "use_idle", True),
        # A server that was verified: without it Thunderbird treats the profile as a first run.
        (s + "valid", True),
        (s + "delete_model", 1),        # a deleted mail moves to Trash
        (s + "directory-rel", "[ProfD]ImapMail/" + mail_dir(a)),
        # The bodies are fetched ahead, so a mail reads at once and with no network.
        (s + "autosync_offline_stores", True),
        (s + "offline_download", True),
        # ... but not the whole of a mailbox that is years deep: the headers of everything are kept, and the
        # body of an older mail is fetched when it is opened.
        (s + "autosync_max_age_days", SYNC_DAYS),
        (s + "max_cached_connections", 2),
        (i + "fullName", a["sender"] or a["name"]),
        (i + "useremail", a["email"]),
        (i + "smtpServer", smtp),
        (i + "valid", True),
        (i + "compose_html", False),    # Bombadil's drafts are plain text
        (i + "reply_on_top", 1),        # the person's words above the quote
        # What messages.archive does: into one Archive folder, not Archive/<year>.
        (i + "archive_granularity", 0),
        # Gmail keeps a copy of whatever goes through its SMTP server; a second one made here would be a
        # duplicate in Sent. Everywhere else the copy is Thunderbird's to make (into the folder the server
        # names as Sent: the folder prefs are left to Thunderbird, which finds them by their special-use flag).
        (i + "fcc", a["provider"] != "google"),
        (t + "hostname", out["host"]),
        (t + "port", out["port"]),
        (t + "username", a["email"]),
        (t + "try_ssl", secure[out["security"]]),
        (t + "authMethod", method),
        (t + "description", a["email"]),
    ]
    return prefs


def render_user_js(accounts: dict[str, dict]) -> str:
    """The whole of user.js for these accounts (a registry's, keyed by account id)."""
    lines = ["// Written by bombadil-mail (bombadil/mail/engine.py) before every start. Thunderbird reads this file at",
             "// each start, so what is changed here by hand is lost; what it learns by itself is in prefs.js.", ""]

    def section(title, prefs):
        lines.append(f"// ---- {title}")
        lines.extend(f"user_pref({json.dumps(k)}, {_js(v)});" for k, v in prefs)
        lines.append("")

    section("quiet", QUIET)
    section("add-on", ADDON)
    mail = sorted(accounts.values(), key=lambda a: a["n"])
    keys = [f"account{LOCAL_FOLDERS}"] + [f"account{a['n']}" for a in mail]
    registry = [("mail.accountmanager.accounts", ",".join(keys)),
                ("mail.accountmanager.localfoldersserver", f"server{LOCAL_FOLDERS}")]
    if mail:
        registry += [("mail.accountmanager.defaultaccount", f"account{mail[0]['n']}"),
                     ("mail.smtpservers", ",".join(f"smtp{a['n']}" for a in mail)),
                     ("mail.smtp.defaultserver", f"smtp{mail[0]['n']}")]
    section("accounts", registry)
    section("local folders", [
        (f"mail.account.account{LOCAL_FOLDERS}.server", f"server{LOCAL_FOLDERS}"),
        (f"mail.server.server{LOCAL_FOLDERS}.type", "none"),
        (f"mail.server.server{LOCAL_FOLDERS}.hostname", "Local Folders"),
        (f"mail.server.server{LOCAL_FOLDERS}.userName", "nobody"),
        (f"mail.server.server{LOCAL_FOLDERS}.name", "Local Folders"),
        (f"mail.server.server{LOCAL_FOLDERS}.directory-rel", "[ProfD]Mail/Local Folders"),
        (f"mail.server.server{LOCAL_FOLDERS}.valid", True),
    ])
    for a in mail:
        section(f"account {a['id']} ({a['provider']})", account_prefs(a))
    return "\n".join(lines).rstrip("\n") + "\n"


def _forgotten_lines(n: int) -> re.Pattern:
    """The prefs.js lines of one account: its account, server, identity and SMTP server, by number."""
    return re.compile(rf'user_pref\("mail\.(?:account\.account|server\.server|identity\.id|smtpserver\.smtp){n}\.')


# What prefs.js says about which accounts exist: user.js says it again at every start, so a forgotten account
# need not be in these, and leaving it in them would keep its number in Thunderbird's own list.
_ACCOUNT_LISTS = re.compile(r'user_pref\("mail\.(?:accountmanager\.(?:accounts|defaultaccount|localfoldersserver)'
                            r'|smtpservers|smtp\.defaultserver)"')


# -- files --

def _write(path: Path, data: bytes, mode: int) -> bool:
    """data into path through a temp file and a rename, unless it is there already. True when it wrote."""
    try:
        if path.read_bytes() == data:
            if stat.S_IMODE(path.stat().st_mode) != mode:
                os.chmod(path, mode)
            return False
    except OSError:
        pass
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), mode)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return True


def _alive(pid: int) -> bool:
    """Is there a process with this pid that has not exited (a zombie has)?"""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        return Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def _kill_group(pgid: int, sig: int = signal.SIGKILL) -> None:
    """A signal for a whole process group, which is never this one's."""
    if pgid > 1 and pgid != os.getpgrp():
        try:
            os.killpg(pgid, sig)
        except OSError:
            pass


def source_digest(source: Path) -> tuple[str, list[Path]]:
    """The add-on's files (regular files, not hidden, not caches) and the digest of their names and bytes."""
    files = sorted(p for p in source.rglob("*") if p.is_file() and not p.is_symlink()
                   and not any(part.startswith(".") or part == "__pycache__"
                               for part in p.relative_to(source).parts))
    h = hashlib.sha256()
    for p in files:
        data = p.read_bytes()
        h.update(f"{p.relative_to(source).as_posix()}\0{len(data)}\0".encode())
        h.update(data)
    return h.hexdigest(), files


def build_xpi(source: Path, target: Path) -> bool:
    """The add-on as an .xpi at `target`, unless the one there was built from this very source (its digest is
    the zip's comment). Fixed timestamps, modes and order, and no compression, so the bytes are the source's
    alone. True when it built one."""
    digest, files = source_digest(source)
    want = XPI_COMMENT + digest.encode()
    try:
        with zipfile.ZipFile(target) as z:
            if z.comment == want:
                return False
    except (OSError, zipfile.BadZipFile):
        pass
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:
            z.comment = want
            for p in files:
                info = zipfile.ZipInfo(p.relative_to(source).as_posix(), ZIP_TIME)
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                z.writestr(info, p.read_bytes())
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return True


class ThunderbirdProcess:
    """Thunderbird, started, stopped, seeded with accounts and shown on request. Every argument is for tests
    and for a machine where something lives elsewhere; the service passes none."""

    def __init__(self, *, binary: str | None = None, addon_dir: Path | None = None, host: Path | None = None,
                 hyprland=None, stop_timeout: float = STOP_S, log_cap: int = LOG_CAP, addon_id: str = ADDON_ID):
        self._binary = binary
        self._addon_dir = addon_dir
        self._host = host
        self._hypr = hyprland
        self.stop_timeout = stop_timeout
        self.log_cap = log_cap
        self.addon_id = addon_id
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._pump: threading.Thread | None = None
        self._headless = False
        self._started = 0.0

    # -- where things are --

    @property
    def profile(self) -> Path:
        return paths.mail_profile()

    @property
    def log_path(self) -> Path:
        return paths.state_dir() / "mail-engine.log"

    def binary(self) -> str | None:
        found = self._binary or os.environ.get("BOMBADIL_MAIL_THUNDERBIRD")
        if found:
            return found if os.path.isfile(found) and os.access(found, os.X_OK) else None
        return shutil.which("thunderbird")

    def addon_dir(self) -> Path:
        if self._addon_dir is not None:
            return Path(self._addon_dir)
        mine = Path(__file__).resolve().parents[3] / "share" / "mail" / "extension"
        installed = paths.share_dir() / "share" / "mail" / "extension"
        return installed if not (mine / "manifest.json").is_file() and (installed / "manifest.json").is_file() else mine

    def host_path(self) -> Path | None:
        """bin/bombadil-mail-host: next to this package's bin/ (a checkout, or the installed tree), else on PATH."""
        if self._host is not None:
            return Path(self._host) if os.access(self._host, os.X_OK) else None
        beside = Path(__file__).resolve().parents[3] / "bin" / "bombadil-mail-host"
        if beside.is_file() and os.access(beside, os.X_OK):
            return beside
        found = shutil.which("bombadil-mail-host")
        return Path(os.path.abspath(found)) if found else None

    def manifest_path(self) -> Path:
        return paths.home() / ".mozilla" / "native-messaging-hosts" / f"{HOST_NAME}.json"

    # -- the interface the service calls --

    def available(self) -> tuple[bool, str]:
        """Can mail be fetched here? Cheap, and the reason is a sentence for the person."""
        if self.binary() is None:
            return False, "Thunderbird is not installed here, so mail cannot be fetched."
        if not (self.addon_dir() / "manifest.json").is_file():
            return False, "Mail's add-on for Thunderbird is missing from this installation."
        if self.host_path() is None:
            return False, "Mail's connection to Thunderbird (bombadil-mail-host) is missing from this installation."
        return True, ""

    def prepare(self) -> None:
        """Everything a start needs, written again only where it differs: the profile, the add-on, its
        permissions, the native-messaging manifest and user.js. Safe to call at any time."""
        with self._lock:
            ok, why = self.available()
            if not ok:
                raise RuntimeError(why)
            self._ensure_profile()
            self._install_addon()
            self._grant_permissions()
            self._write_manifest()
            self._render()
            self._scrub()

    def seed_account(self, account: dict, provider: accts.Provider) -> None:
        """Write the account into Thunderbird's settings, effective at its next start. Seeding the same address
        again changes nothing. The same address under another account id replaces the old one. An account id that
        comes back for another address or another server (ids restart if Mail's own notes were lost) is no longer
        the account Thunderbird knows by that number: what Thunderbird keeps under it, its folder and the folder
        prefs it learned, is cleared as a forgotten account's is, and the number starts again."""
        with self._lock:
            entry = entry_for(account, provider)
            self._ensure_profile()
            registry = self._registry()
            accounts = registry["accounts"]
            for old in [a for a in accounts.values() if a["id"] != entry["id"] and a["email"] == entry["email"]]:
                self._tombstone(registry, accounts.pop(old["id"]))
            previous = accounts.get(entry["id"])
            if previous is not None and (previous["email"] != entry["email"] or mail_dir(previous) != mail_dir(entry)):
                self._tombstone(registry, previous, reused=True)
            accounts[entry["id"]] = entry
            self._save(registry)
            self._render(registry)
            self._scrub()

    def forget_account(self, account: dict) -> None:
        """Take the account out of Thunderbird for good. user.js stops listing it at once, so the next start does
        not load it. What prefs.js, logins.json and ImapMail still hold for it, and Thunderbird's caches that
        name it (see `_scrub_caches`), is removed now when Thunderbird is not running, else when it is next
        stopped or started: it writes what it holds back at exit. Forgetting what is not known is nothing.

        What stays: a saved password or sign-in token whose login another account at the same server or the
        same provider's sign-in page still uses, until the last of those accounts is forgotten (Thunderbird keys
        a login by its page and an encrypted user name, which cannot be read here without its crypto library, so
        a login of the same page cannot be told from the account's own). The person's address book and the
        addresses it collected from sent mail are the person's and are not account data."""
        with self._lock:
            registry = self._registry()
            email = str(account.get("email") or "").strip().lower()
            aid = str(account.get("id"))
            for a in [a for a in registry["accounts"].values() if a["id"] == aid or (email and a["email"] == email)]:
                self._tombstone(registry, registry["accounts"].pop(a["id"]))
                self._save(registry)
            self._render(registry)
            self._scrub()

    def start(self) -> None:
        """Spawn Thunderbird and return: the service waits for the add-on's hello. Nothing happens when one is
        running already (this one's or an earlier run's, which holds the profile's lock)."""
        with self._lock:
            if self.running():
                return
            self._finish()
            ok, why = self.available()
            if not ok:
                raise RuntimeError(why)
            self._ensure_profile()
            self._scrub()
            env = dict(os.environ)
            kind = display_kind(env)
            argv = [self.binary(), "--profile", str(self.profile), "--no-remote"]
            if kind is None:
                argv.append("--headless")
            try:
                logfile = self._open_log()
                self._proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                              stderr=subprocess.STDOUT, env=self._child_env(env, kind),
                                              start_new_session=True, close_fds=True)
            except OSError as e:
                raise RuntimeError(f"Thunderbird could not be started ({e.strerror or e.__class__.__name__}).") from e
            self._headless = kind is None
            self._started = time.monotonic()
            self._pump = threading.Thread(target=self._drain, args=(self._proc.stdout, logfile), daemon=True,
                                          name="mail-engine-log")
            self._pump.start()

    def stop(self) -> None:
        """Ask Thunderbird to quit and give it `stop_timeout` seconds, then kill its whole process group. A
        no-op when it is not running; it never raises."""
        self._stop(self.stop_timeout)

    def _stop(self, patience: float) -> None:
        with self._lock:
            proc, pid = self._proc, None
            if proc is not None and proc.poll() is None:
                pid = proc.pid
            elif proc is None:
                pid = self._holder()
            if pid is not None:
                self._terminate(pid, proc, patience)
            self._finish()
            try:
                self._scrub()
            except Exception as e:  # noqa: BLE001 - a stop that cannot tidy is still a stop
                log(f"clearing what a removed account left behind: {type(e).__name__}: {e}")

    def restart(self) -> None:
        """Stop and start, from whatever state it is in: running, stopped, crashed, or left over from an earlier
        service run. This is also how a stuck modal dialog is cleared (a send that failed, a question nobody
        sees): Thunderbird is stopped, killed if it will not go, and started again. It starts in the mode
        `start` picks now, so a service that has a display gets a window, and one that has none, none."""
        with self._lock:
            self.stop()
            self.start()

    def running(self) -> bool:
        """Is a Thunderbird alive on the profile? Ours is polled (and reaped); one that an earlier run of the
        service left holding the lock counts too, since it will reconnect by itself and a second one on the
        same profile would only be refused."""
        with self._lock:
            if self._proc is not None:
                if self._proc.poll() is None:
                    return True
                self._finish()
                return self._holder() is not None
            return self._holder() is not None

    def stage(self, on: bool) -> bool:
        """Show Thunderbird's window (for a sign-in, an app password, anything it must ask a person) or put it
        away. True when it is as asked, False when it cannot be: Hyprland is not there, there is no window (yet),
        or Thunderbird has a message open for sending.

        The windows live on the special workspace `special:mail-engine`, which the compositor slides in over the
        one in use, as it does for the other panels; staging focuses the one window worth showing (a dialog
        before the main window, by its own address) and so shows that workspace, and putting away toggles it out
        again. Nothing is moved, so nothing can be left stranded on a workspace if the service goes away, and a
        Thunderbird that opens another window while it is shown gets it in the same place. But showing the
        workspace shows every window on it, and a compose window has a Send button of Thunderbird's own, which is
        a send that is not Mail's press: with one open, staging is False and shows nothing. (A restart clears it.)

        It returns within a few seconds whatever happens (STAGE_WAIT_S and HYPR_S), because the service calls it
        with a budget of five. A Thunderbird that was started headless (no display then) has no window to show:
        with a display now, staging starts it again with one, waits what is left of the budget for the window and
        is False if it has not mapped by then; with no display it is False. A False from a window that was still
        on its way is answered by asking again."""
        h = self._hypr or hypr.Hyprland()
        try:
            if not h.available:
                return False
            return self._show(h) if on else self._hide(h)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as e:
            # a compositor that hangs (hyprctl's own timeout is a SubprocessError) or answers oddly is no window
            log(f"staging Thunderbird's window: {type(e).__name__}: {e}")
            return False

    # -- staging --

    def _show(self, h) -> bool:
        entered = time.monotonic()
        with self._lock:
            if not self.running():
                return False
            if self._headless and self._proc is not None:
                if display_kind(os.environ) is None:
                    return False
                self._stop(STAGE_QUIT_S)
                self.start()
            # a window takes a moment to map after a start, but never more of this call than its budget
            deadline = min(self._started + WINDOW_WAIT_S, entered + STAGE_WAIT_S) if self._started else entered
        while True:
            clients = json.loads(h.request("j/clients", timeout=HYPR_S))
            if compose_windows(clients):
                log("a message is open for sending in Thunderbird, so its window is not shown")
                return False
            window = pick_window(clients)
            if window is not None:
                return self._focus(h, window)
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.25)

    @staticmethod
    def _focus(h, window: dict) -> bool:
        """Focus the window by its address; where the compositor refuses that, by its pid (understood, as the
        launcher uses it: the main window then, which is what the dialog is in front of). Only a refusal is
        retried, never a hang, so the call's time is not spent twice. A compose window is not one of these:
        `_show` has refused before there is one on screen."""
        *first, last = window_selectors(window)
        for selector in first:
            try:
                h.dispatch(f"hl.dsp.focus({{ window = {lua_str(selector)} }})")
                return True
            except RuntimeError as e:
                log(f"focusing Thunderbird's window by {selector}: {e}")
        h.dispatch(f"hl.dsp.focus({{ window = {lua_str(last)} }})")
        return True

    def _hide(self, h) -> bool:
        shown = staged_monitor(json.loads(h.request("j/monitors", timeout=HYPR_S)))
        if shown is None:
            return True
        if not shown.get("focused", True):   # toggle_special acts on the focused monitor
            h.dispatch(f"hl.dsp.focus({{ monitor = {lua_str(shown.get('name', ''))} }})")
        h.dispatch(f"hl.dsp.workspace.toggle_special({lua_str(ENGINE_WORKSPACE.removeprefix('special:'))})")
        return True

    # -- the process --

    def _own_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _child_env(self, env: dict, kind: str | None) -> dict:
        env = {k: v for k, v in env.items() if not k.startswith("MOZ_") and k not in _DROP_ENV}
        env["MOZ_CRASHREPORTER_DISABLE"] = "1"      # a crash is a restart, not a dialog
        env["MOZ_CRASHREPORTER_NO_REPORT"] = "1"
        env["NO_AT_BRIDGE"] = "1"                   # no accessibility bus chatter
        if kind == "wayland":
            env["MOZ_ENABLE_WAYLAND"] = "1"
        elif kind == "x11":
            env.pop("WAYLAND_DISPLAY", None)        # a dead Wayland socket must not win over a good X display
        else:
            env.pop("WAYLAND_DISPLAY", None)
            env.pop("DISPLAY", None)
        return env

    def _holder(self) -> int | None:
        """The pid of a live Thunderbird on this profile that is not our child: whose pid the profile's lock names,
        if that process is a Thunderbird of this user started on this profile (a pid is not trusted to be the
        same program that wrote it). None for no lock, a stale one (a crash leaves it) or someone else's."""
        try:
            held = os.readlink(self.profile / "lock")
            pid = int(_LOCK.fullmatch(held).group(1))
            if not _alive(pid) or os.stat(f"/proc/{pid}").st_uid != os.getuid():
                return None
            argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        except (OSError, AttributeError, ValueError):
            return None
        if not os.path.basename(argv[0]).startswith(b"thunderbird"):
            return None
        mine = os.path.realpath(self.profile)
        for flag, value in pairwise(argv):
            if flag in (b"--profile", b"-profile") and os.path.realpath(os.fsdecode(value)) == mine:
                return pid
        return None

    def _terminate(self, pid: int, proc: subprocess.Popen | None, patience: float) -> None:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except PermissionError:
            return
        deadline = time.monotonic() + patience
        while time.monotonic() < deadline:
            if (proc.poll() is not None) if proc is not None else not _alive(pid):
                return
            time.sleep(0.05)
        log("Thunderbird did not quit when asked; killing it")
        if proc is not None or self._pgid(pid) == pid:
            _kill_group(pid)
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        if proc is not None:
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                log("Thunderbird could not be killed")
        else:
            end = time.monotonic() + 5
            while _alive(pid) and time.monotonic() < end:
                time.sleep(0.05)

    @staticmethod
    def _pgid(pid: int) -> int | None:
        try:
            return os.getpgid(pid)
        except OSError:
            return None

    def _finish(self) -> None:
        """Our child is gone or about to be: reap it, kill what it left in its group (a content process that
        outlived the main one), and let the log pump end."""
        proc, pump = self._proc, self._pump
        self._proc = self._pump = None
        if proc is None:
            return
        _kill_group(proc.pid)   # the group id is the child's pid: it started a session
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            log("Thunderbird could not be reaped")
        if pump is not None:
            pump.join(2)
        if pump is None or not pump.is_alive():
            try:
                proc.stdout.close()
            except (OSError, AttributeError):
                pass

    # -- Thunderbird's output --

    def _open_log(self):
        path = self.log_path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        return os.fdopen(fd, "ab", buffering=0)

    def _drain(self, stream, logfile) -> None:
        """Copy Thunderbird's output into the log until it ends, and always read it: a pipe nobody reads stops
        Thunderbird. The log is `log_cap` bytes at most (one older file is kept beside it). Failing to write
        loses output, never the reading."""
        try:
            size = os.fstat(logfile.fileno()).st_size
        except OSError:
            size = 0
        try:
            while True:
                chunk = stream.read1(65536)
                if not chunk:
                    break
                try:
                    if size + len(chunk) > self.log_cap:
                        logfile.close()
                        os.replace(self.log_path, self.log_path.with_name(self.log_path.name + ".1"))
                        logfile = self._open_log()
                        size = 0
                    logfile.write(chunk[: self.log_cap])
                    size += min(len(chunk), self.log_cap)
                except (OSError, ValueError):
                    pass
        except (OSError, ValueError):
            pass
        finally:
            try:
                logfile.close()
            except OSError:
                pass

    # -- the profile --

    def _ensure_profile(self) -> None:
        profile = self.profile
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(profile, 0o700)   # it holds a copy of the person's mail
        (profile / "extensions").mkdir(exist_ok=True, mode=0o700)

    def _install_addon(self) -> None:
        source = self.addon_dir()
        try:
            manifest = json.loads((source / "manifest.json").read_text())
            declared = ((manifest.get("browser_specific_settings") or {}).get("gecko") or {}).get("id")
        except (OSError, ValueError, AttributeError) as e:
            raise RuntimeError("Mail's add-on for Thunderbird cannot be read.") from e
        if declared != self.addon_id:
            raise RuntimeError("Mail's add-on for Thunderbird has an id that the installation does not expect.")
        build_xpi(source, self.profile / "extensions" / f"{self.addon_id}.xpi")

    def _grant_permissions(self) -> None:
        """A permission a manifest lists as optional cannot be granted from the manifest; Thunderbird keeps grants
        in extension-preferences.json, and the add-on is Bombadil's own, so its own list is what is granted."""
        try:
            optional = json.loads((self.addon_dir() / "manifest.json").read_text()).get("optional_permissions") or []
        except (OSError, ValueError, AttributeError):
            return
        optional = sorted(p for p in optional if isinstance(p, str))
        path = self.profile / "extension-preferences.json"
        try:
            data = json.loads(path.read_text())
            data = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            data = {}
        mine = data.get(self.addon_id) if isinstance(data.get(self.addon_id), dict) else {}
        if sorted(mine.get("permissions") or []) == optional and (optional or self.addon_id not in data):
            return
        if optional:
            data[self.addon_id] = {**mine, "permissions": optional, "origins": mine.get("origins") or [],
                                   "data_collection": mine.get("data_collection") or []}
        else:
            data.pop(self.addon_id, None)
        _write(path, json.dumps(data).encode(), 0o600)

    def _write_manifest(self) -> None:
        host = self.host_path()
        if host is None:
            raise RuntimeError("Mail's connection to Thunderbird (bombadil-mail-host) is missing from this installation.")
        path = self.manifest_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        manifest = {"name": HOST_NAME, "description": "Bombadil Mail: carries the add-on's words to the mail service",
                    "path": str(host), "type": "stdio", "allowed_extensions": [self.addon_id]}
        _write(path, (json.dumps(manifest, indent=2) + "\n").encode(), 0o644)

    # -- accounts --

    def _registry(self) -> dict:
        try:
            data = json.loads((self.profile / REGISTRY).read_text())
            stored, left = data["accounts"], [x for x in data.get("forgotten", []) if isinstance(x, dict)]
            if not isinstance(stored, dict):
                raise TypeError("accounts")
        except (OSError, ValueError, KeyError, AttributeError, TypeError):
            return {"accounts": {}, "forgotten": []}
        accounts = {}
        for key, value in stored.items():
            try:
                accounts[key] = checked = _checked(value)
                if checked["id"] != key:
                    raise ValueError("an account under another's id")
            except ValueError as e:
                log(f"leaving an account out of {REGISTRY}: {e}")
                accounts.pop(key, None)
        return {"accounts": accounts, "forgotten": left[-MAX_FORGOTTEN:]}

    def _save(self, registry: dict) -> None:
        _write(self.profile / REGISTRY, (json.dumps(registry, indent=1, sort_keys=True) + "\n").encode(), 0o600)

    def _render(self, registry: dict | None = None) -> None:
        registry = registry if registry is not None else self._registry()
        _write(self.profile / "user.js", render_user_js(registry["accounts"]).encode(), 0o600)

    def _tombstone(self, registry: dict, gone: dict, reused: bool = False) -> None:
        """Note what an account that is gone from user.js left in Thunderbird, for `_scrub`: its number (its
        prefs), its folder, and the logins of its servers and its sign-in page. `reused` says that the number is
        live again for another address or server, so that the note is for what Thunderbird learned under it
        (its folder and its prefs.js lines), which user.js does not say again, and not a number that is gone."""
        logins = {f"{side}://{gone[side]['host']}" for side in ("imap", "smtp")}
        if gone["provider"] in _ISSUER:
            logins.add(f"oauth://{_ISSUER[gone['provider']]}")
        note = {"n": gone["n"], "dir": mail_dir(gone), "logins": sorted(logins)}
        if reused:
            note["reused"] = True
        registry["forgotten"] = (registry["forgotten"] + [note])[-MAX_FORGOTTEN:]

    def _scrub(self) -> None:
        """Finish forgetting: remove what removed accounts left in prefs.js, logins.json, ImapMail and the caches.
        Only while Thunderbird is not running (it holds all of it and writes it back), and never what an account
        that is in the registry now uses: a number, a folder or a login that came back into use is left alone,
        except for a number that came back for another address (`reused`), whose old folder and prefs are the
        old account's and not the new one's."""
        registry = self._registry()
        pending = registry["forgotten"]
        if not pending or self._holder() is not None or self._own_alive():
            return
        live = list(registry["accounts"].values())
        numbers = {a["n"] for a in live}
        folders = {mail_dir(a) for a in live}
        in_use = {f"{side}://{a[side]['host']}" for a in live for side in ("imap", "smtp")}
        in_use |= {f"oauth://{_ISSUER[a['provider']]}" for a in live if a["provider"] in _ISSUER}
        failed = []
        for item in pending:
            try:
                n = int(item["n"])
                reused = item.get("reused") is True
                if n > LOCAL_FOLDERS and (reused or n not in numbers):   # Local Folders is no account's to forget
                    self._scrub_prefs(n)
                self._scrub_logins([h for h in item.get("logins", []) if isinstance(h, str) and h not in in_use
                                    and h.startswith(("imap://", "smtp://", "oauth://"))])   # a note names mail's only
                if reused or item.get("dir") not in folders:
                    self._scrub_mail(str(item.get("dir")))
                self._scrub_caches()
            except OSError as e:   # a file that could not be written now may be at the next try
                log(f"clearing a removed account from the profile: {type(e).__name__}: {e}")
                failed.append(item)
            except (ValueError, KeyError, TypeError) as e:   # a note that makes no sense never will: drop it
                log(f"dropping a note about a removed account that cannot be read: {type(e).__name__}: {e}")
        if failed != pending:
            registry["forgotten"] = failed
            self._save(registry)

    def _scrub_caches(self) -> None:
        """Thunderbird's own files that name an account's server or keep what it fetched, and that it makes again by
        itself: the global index (made empty now that the indexer is off, but a profile from before may hold
        mail text in it), the folder tree and the folder cache. They are not the account's alone, so a removed
        account takes all of them, and what Thunderbird needs of them it learns again."""
        for name in _CACHES:
            try:
                (self.profile / name).unlink()
            except FileNotFoundError:
                pass

    def _scrub_prefs(self, n: int) -> None:
        path = self.profile / "prefs.js"
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        own = _forgotten_lines(n)
        lines = text.splitlines(keepends=True)
        kept = [ln for ln in lines if not own.match(ln) and not _ACCOUNT_LISTS.match(ln)]
        if len(kept) != len(lines):
            _write(path, "".join(kept).encode("utf-8"), stat.S_IMODE(path.stat().st_mode))

    def _scrub_logins(self, hosts: list[str]) -> None:
        path = self.profile / "logins.json"
        if not hosts:
            return
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError:
            return
        except ValueError:   # not JSON: Thunderbird cannot read it either, and there is nothing here to repair
            log("logins.json cannot be read; leaving it as it is")
            return
        logins = data.get("logins") if isinstance(data, dict) else None
        if not isinstance(logins, list):
            return
        kept = [x for x in logins if not (isinstance(x, dict) and x.get("hostname") in hosts)]
        if len(kept) != len(logins):
            data["logins"] = kept
            _write(path, json.dumps(data).encode(), stat.S_IMODE(path.stat().st_mode))

    def _scrub_mail(self, name: str) -> None:
        """The account's copy of its mail: one folder of ImapMail, by a name made here, never through a link."""
        if _MAIL_DIR.fullmatch(name) is None:
            return
        root = self.profile / "ImapMail"
        folder = root / name
        if folder.is_symlink():
            folder.unlink()
        elif folder.is_dir():
            shutil.rmtree(folder)
        try:
            (root / f"{name}.msf").unlink()   # the server's own summary file, which sits beside its folder
        except FileNotFoundError:
            pass

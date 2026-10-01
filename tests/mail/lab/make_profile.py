#!/usr/bin/env python3
"""Create a Thunderbird profile directory for the lab: user.js + logins.json + extension.

This is the lab prototype of the production "write an account into Thunderbird
from an address" code. Every pref below was needed (or deliberately set) for a
Thunderbird 157 release build to start with NO human interaction: no first-run
tab / Account Hub, no default-client prompt, no start page, no "know your
rights" bar, no telemetry or update traffic, no new-mail popup, no sign-in
prompt. Notes on each pref are in the PREFS_* tables: keep them in sync with
FINDINGS.md.

CLI:
    make_profile.py PROFILE_DIR [--kind lab|gmail|outlook|icloud|none]
                    [--email ADDR] [--password PW] [--imap-port N] [--smtp-port N]
                    [--load profile-xpi|profile-dir|none|...] [--xpi FILE]
                    [--nss-dir DIR] [--extra-user-js FILE]
Library:
    from make_profile import write_profile
"""
import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXT_ID = "bombadil-mail-lab@bombadil.lab"

# --------------------------------------------------------------------------- app-level prefs
# Each entry: pref, value, why.
PREFS_QUIET = [
    # --- first run / windows that must never appear --------------------------------
    ("mail.provider.suppress_dialog_on_startup", True,
     "messenger.js verifyExistingAccounts()/FirstRun.isFirstRun(): with no account Thunderbird opens the "
     "Account Hub tab at startup. With this pref it never does, and it also stops Thunderbird creating the "
     "Local Folders account itself (so we write that account ourselves, see ACCOUNT prefs)."),
    ("mail.shell.checkDefaultClient", False,
     "Default-mail-client prompt on first start (MailGlue). Also set mail.shell.checkDefaultCalendar."),
    ("mail.spotlight.firstRunDone", True, "macOS Spotlight integration dialog (harmless on Linux)."),
    ("mail.winsearch.firstRunDone", True, "Windows search integration dialog (harmless on Linux)."),
    ("mailnews.start_page.enabled", False,
     "Start page in the message pane (was live.thunderbird.net, network fetch + cert errors behind a proxy)."),
    ("mail.rights.version", 1,
     "'Thunderbird is free and open source software... Know your rights' notification bar (shown while < 1)."),
    ("mail.chat.enabled", False, "Chat (IRC/XMPP) module and its tab; saves memory and a startup path."),
    ("mail.inappnotifications.enabled", False,
     "In-app notification service (fetches notifications.thunderbird.net every 6h, shows donation/blog banners)."),
    ("mail.biff.show_alert", False, "New-mail popup window (newmailalert.xhtml). Bombadil's shell notifies instead."),
    ("mail.biff.play_sound", False, "New-mail sound."),
    ("mail.biff.use_system_alert", False, "No libnotify notification for new mail either."),
    ("mail.biff.show_tray_icon_always", False, "No tray icon."),
    ("mail.biff.show_badge", False, "No badge/count in the dock/tray."),
    # --- network traffic we do not want -------------------------------------------
    ("app.update.enabled", False, "Release channel would check for updates (updater is irrelevant for a distro package)."),
    ("app.update.auto", False, "No background download of updates."),
    ("app.update.staging.enabled", False, "No update staging."),
    ("app.update.checkInstallTime", False, "No 'this build is N days old' nag (MailGlue)."),
    ("extensions.update.enabled", False, "No add-on update checks (lab add-on has no update_url)."),
    ("extensions.update.autoUpdateDefault", False, "Same."),
    ("extensions.blocklist.enabled", False, "No blocklist fetch from remote settings."),
    ("datareporting.policy.dataSubmissionEnabled", False, "Telemetry upload policy off (also stops the data-choices notice)."),
    ("datareporting.healthreport.uploadEnabled", False, "Health report upload off."),
    ("toolkit.telemetry.enabled", False, "Telemetry off."),
    ("toolkit.telemetry.unified", False, "Telemetry off."),
    ("toolkit.telemetry.archive.enabled", False, "Telemetry off."),
    ("toolkit.telemetry.server", "", "Telemetry server blank."),
    ("toolkit.telemetry.reportingpolicy.firstRun", False, "No first-run telemetry policy tab."),
    ("datareporting.policy.firstRunURL", "", "No privacy-policy tab on first run."),
    ("browser.crashReports.unsubmittedCheck.enabled", False, "No 'unsent crash report' bar."),
    ("network.proxy.type", 0, "Direct connections (system proxy settings must not apply to 127.0.0.1 mail ports)."),
    ("network.captive-portal-service.enabled", False, "No captive-portal probe traffic."),
    ("network.connectivity-service.enabled", False, "No connectivity probe traffic."),
    ("browser.safebrowsing.malware.enabled", False, "No Safe Browsing list downloads."),
    ("browser.safebrowsing.phishing.enabled", False, "No Safe Browsing list downloads."),
    # --- behaviour of the background add-on -------------------------------------------
    ("general.useragent.locale", "en-US", "Stable locale for headless operation."),
    ("mail.server.default.check_new_mail", True, "Poll/IDLE for new mail (per-server value below also set)."),
]

# Add-on loading. Release Thunderbird 157 does NOT enforce signing (MOZ_REQUIRE_SIGNING=false,
# xpinstall.signatures.required defaults to false), but a sideloaded add-on is auto-disabled unless
# the scope is enabled: extensions.autoDisableScopes default is 15 (all scopes).
PREFS_ADDON = [
    ("extensions.autoDisableScopes", 0,
     "Default 15 = every scope's new add-ons start disabled awaiting user consent. 0 = enable add-ons found in any scope "
     "(profile extensions/ dir, distribution/extensions) without a prompt."),
    ("extensions.enabledScopes", 15, "All install scopes are scanned (1 profile, 2 user, 4 app, 8 system)."),
    ("extensions.startupScanScopes", 15,
     "Default 4 = only the app scope is rescanned at every startup; profile-dir add-ons are then only picked up "
     "on first run / version change. 15 makes replacing <profile>/extensions/<id>.xpi take effect on next start."),
    ("xpinstall.signatures.required", False, "Explicit: unsigned add-ons allowed (already the default in TB 157 release)."),
]

PREFS_DEBUG = [
    ("extensions.logging.enabled", True, "AddonManager/XPIProvider logs to the browser console."),
    ("devtools.console.stdout.chrome", True, "Copy chrome console.* to stdout (tb.log)."),
    ("devtools.console.stdout.content", True, "Copy add-on background page console.* to stdout."),
    ("browser.dom.window.dump.enabled", True, "dump() to stdout."),
]


# --------------------------------------------------------------------------- account kinds
def kind_for_address(email):
    """Choose the account template from the domain (same knowledge as the ISP DB, see FINDINGS.md)."""
    dom = email.rsplit("@", 1)[-1].lower()
    if dom in ("gmail.com", "googlemail.com"):
        return "gmail"
    if dom in ("outlook.com", "hotmail.com", "live.com", "msn.com", "office365.com", "onmicrosoft.com") or \
            dom.startswith(("outlook.", "hotmail.", "live.")):
        return "outlook"
    if dom in ("icloud.com", "me.com", "mac.com"):
        return "icloud"
    return "generic"


# nsMsgAuthMethod: 1 none, 3 password-cleartext (PLAIN/LOGIN, fine over TLS), 4 password-encrypted (CRAM-MD5),
# 5 GSSAPI, 6 NTLM, 7 TLS client cert, 8 any secure, 9 any, 10 OAuth2.
# nsMsgSocketType: 0 plain, 1 try STARTTLS, 2 always STARTTLS, 3 SSL/TLS.
def account_template(kind, email, imap_port, smtp_port):
    local = email.split("@")[0]
    if kind == "lab":
        return dict(imap_host="127.0.0.1", imap_port=imap_port, imap_socket=0, imap_auth=3, imap_user=email,
                    smtp_host="127.0.0.1", smtp_port=smtp_port, smtp_socket=0, smtp_auth=3, smtp_user=email,
                    oauth=None)
    if kind == "gmail":
        return dict(imap_host="imap.gmail.com", imap_port=993, imap_socket=3, imap_auth=10, imap_user=email,
                    smtp_host="smtp.gmail.com", smtp_port=465, smtp_socket=3, smtp_auth=10, smtp_user=email,
                    oauth=dict(issuer="accounts.google.com"))
    if kind == "outlook":
        return dict(imap_host="outlook.office365.com", imap_port=993, imap_socket=3, imap_auth=10, imap_user=email,
                    smtp_host="smtp.office365.com", smtp_port=587, smtp_socket=2, smtp_auth=10, smtp_user=email,
                    oauth=dict(issuer="login.microsoftonline.com"))
    if kind == "icloud":  # app-specific password; IMAP user is the local part, SMTP user the full address
        return dict(imap_host="imap.mail.me.com", imap_port=993, imap_socket=3, imap_auth=3, imap_user=local,
                    smtp_host="smtp.mail.me.com", smtp_port=587, smtp_socket=2, smtp_auth=3, smtp_user=email,
                    oauth=None)
    raise ValueError("generic accounts need an autoconfig lookup (https://autoconfig.thunderbird.net/v1.1/<domain>)")


def _q(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    return '"%s"' % str(v).replace("\\", "\\\\").replace('"', '\\"')


def account_prefs(email, fullname, t):
    """The prefs that make ONE IMAP account + SMTP identity (+ Local Folders) in prefs.js/user.js.

    Keys (account1 = the mail account, account2 = Local Folders; server1/server2, id1, smtp1 likewise):
    """
    imap_enc = t["imap_user"].replace("@", "%40")
    p = []
    a = p.append
    # registry: which accounts exist
    a(("mail.accountmanager.accounts", "account1,account2", "comma separated account keys"))
    a(("mail.accountmanager.defaultaccount", "account1", "account that composes/sends by default"))
    a(("mail.accountmanager.localfoldersserver", "server2", "server key of Local Folders (Outbox, local Trash, Templates)"))
    a(("mail.account.account1.server", "server1", "account -> incoming server"))
    a(("mail.account.account1.identities", "id1", "account -> identities (comma separated)"))
    a(("mail.account.account2.server", "server2", "Local Folders account -> its server"))
    # incoming server
    a(("mail.server.server1.type", "imap", "protocol: imap | pop3 | none (local) | nntp | rss"))
    a(("mail.server.server1.hostname", t["imap_host"], "IMAP host"))
    a(("mail.server.server1.port", t["imap_port"], "IMAP port"))
    a(("mail.server.server1.userName", t["imap_user"], "IMAP login name (login-manager key is imap://<host>)"))
    a(("mail.server.server1.name", email, "display name of the account in the folder pane (pretty name)"))
    a(("mail.server.server1.socketType", t["imap_socket"], "0 plain, 2 STARTTLS, 3 SSL/TLS"))
    a(("mail.server.server1.authMethod", t["imap_auth"], "3 password, 10 OAuth2"))
    a(("mail.server.server1.login_at_startup", True, "connect right away instead of on first folder open"))
    a(("mail.server.server1.check_new_mail", True, "periodic new-mail check (in addition to IDLE)"))
    a(("mail.server.server1.check_time", 1, "minutes between checks; IDLE covers the gaps"))
    a(("mail.server.server1.use_idle", True, "IMAP IDLE for the open folder (Inbox)"))
    a(("mail.server.server1.valid", True, "server was verified: required so isFirstRun() is false"))
    a(("mail.server.server1.delete_model", 1, "0 flag deleted, 1 move to Trash, 2 remove immediately"))
    a(("mail.server.server1.clientid", "00000000-0000-4000-8000-00000000b0b0", "IMAP CLIENTID sent with ID (any UUID)"))
    a(("mail.server.server1.directory-rel", "[ProfD]ImapMail/" + t["imap_host"], "message store, relative to the profile"))
    a(("mail.server.server1.autosync_offline_stores", True, "download message bodies in the background (offline store)"))
    a(("mail.server.server1.offline_download", True, "same, for new folders"))
    a(("mail.server.server1.max_cached_connections", 2, "IMAP connections kept open"))
    # Local Folders
    a(("mail.server.server2.type", "none", "Local Folders"))
    a(("mail.server.server2.hostname", "Local Folders", ""))
    a(("mail.server.server2.userName", "nobody", ""))
    a(("mail.server.server2.name", "Local Folders", ""))
    a(("mail.server.server2.directory-rel", "[ProfD]Mail/Local Folders", ""))
    a(("mail.server.server2.valid", True, ""))
    # identity
    a(("mail.identity.id1.fullName", fullname, "From: display name"))
    a(("mail.identity.id1.useremail", email, "From: address"))
    a(("mail.identity.id1.smtpServer", "smtp1", "outgoing server for this identity"))
    a(("mail.identity.id1.valid", True, "identity verified"))
    a(("mail.identity.id1.compose_html", False, "compose plain text by default (deterministic bodies for tests)"))
    a(("mail.identity.id1.fcc", True, "save a copy of sent mail"))
    a(("mail.identity.id1.fcc_folder", "imap://%s@%s/Sent" % (imap_enc, t["imap_host"]), "Sent folder URI (IMAP APPEND)"))
    a(("mail.identity.id1.drafts_folder", "imap://%s@%s/Drafts" % (imap_enc, t["imap_host"]), "Drafts folder URI"))
    a(("mail.identity.id1.archive_folder", "imap://%s@%s/Archive" % (imap_enc, t["imap_host"]), "Archive folder URI"))
    a(("mail.identity.id1.archive_enabled", True, "Archive command available"))
    a(("mail.identity.id1.stationery_folder", "imap://%s@%s/Templates" % (imap_enc, t["imap_host"]), "Templates folder URI"))
    a(("mail.identity.id1.reply_on_top", 1, "0 below quote, 1 above quote"))
    # SMTP server
    a(("mail.smtpservers", "smtp1", "comma separated outgoing-server keys"))
    a(("mail.smtp.defaultserver", "smtp1", "default outgoing server"))
    a(("mail.smtpserver.smtp1.hostname", t["smtp_host"], "SMTP host"))
    a(("mail.smtpserver.smtp1.port", t["smtp_port"], "SMTP port"))
    a(("mail.smtpserver.smtp1.username", t["smtp_user"], "SMTP login (login-manager key is smtp://<host>)"))
    a(("mail.smtpserver.smtp1.try_ssl", t["smtp_socket"], "0 plain, 2 STARTTLS, 3 SSL/TLS (same numbers as IMAP socketType)"))
    a(("mail.smtpserver.smtp1.authMethod", t["smtp_auth"], "3 password, 10 OAuth2"))
    a(("mail.smtpserver.smtp1.description", email, "label"))
    a(("mail.smtpserver.smtp1.clientid", "00000000-0000-4000-8000-00000000b0b1", "CLIENTID"))
    if t["oauth"]:
        # With authMethod 10 Thunderbird picks issuer/scopes/client-id from OAuth2Providers.sys.mjs by HOSTNAME
        # (imap.gmail.com -> accounts.google.com, outlook.office365.com -> login.microsoftonline.com): nothing else to set.
        # Custom OAuth client (your own client id) per server, honoured by OAuth2CustomDetails.sys.mjs:
        #   mail.server.server1.oauth2.useCustomDetails=true  .oauth2.clientId .oauth2.scopes
        #   .oauth2.authorizationEndpoint .oauth2.tokenEndpoint .oauth2.redirectionEndpoint .oauth2.usePKCE ...
        pass
    return p


def write_user_js(profile, email, fullname, kind, imap_port, smtp_port, addon_prefs, debug=True, extra=None,
                  quiet=True, ablate=()):
    t = account_template(kind, email, imap_port, smtp_port) if kind != "none" else None
    lines = ["// generated by tests/mail/lab/make_profile.py (kind=%s). Do not edit." % kind, ""]

    def section(title, prefs):
        lines.append("// ---- " + title)
        for item in prefs:
            k, v, why = item
            lines.append("// %s" % why)
            lines.append("user_pref(%s, %s);" % (_q(k), _q(v)))
        lines.append("")

    if quiet:
        section("quiet / no first-run / no phone-home",
                [p for p in PREFS_QUIET + [("mail.shell.checkDefaultCalendar", False, "same, calendar")] if p[0] not in ablate])
    if addon_prefs:
        section("add-on loading", PREFS_ADDON)
    if debug:
        section("lab logging", PREFS_DEBUG)
    if t:
        section("ONE IMAP account + SMTP identity + Local Folders", account_prefs(email, fullname, t))
    if extra:
        lines.append("// ---- extra")
        lines.append(open(extra).read())
    with open(os.path.join(profile, "user.js"), "w") as f:
        f.write("\n".join(lines) + "\n")
    return t


def write_profile(profile, kind="lab", email="test@example.test", password="lab", fullname="Lab Tester",
                  imap_port=1143, smtp_port=1025, load="profile-xpi", xpi=None, nss_dir=None, extra_user_js=None,
                  debug=True, addon_prefs=None, optional_permissions=("messages.send",), quiet=True, ablate=()):
    os.makedirs(profile, exist_ok=True)
    if addon_prefs is None:
        addon_prefs = load in ("profile-xpi", "profile-dir")
    t = write_user_js(profile, email, fullname, kind, imap_port, smtp_port, addon_prefs, debug, extra_user_js, quiet, ablate)
    if t and password and not t["oauth"]:
        import seed_logins
        seed_logins.seed(profile, [
            ("imap://" + t["imap_host"], "imap://" + t["imap_host"], t["imap_user"], password),
            ("smtp://" + t["smtp_host"], "smtp://" + t["smtp_host"], t["smtp_user"], password),
        ], nss_dir)
    if optional_permissions:
        # OptionalOnly permissions (messages.send) cannot be granted from the manifest: the add-on would have to call
        # permissions.request() from a user-input handler (prompt). Thunderbird remembers grants in this file.
        import json
        with open(os.path.join(profile, "extension-preferences.json"), "w") as f:
            json.dump({EXT_ID: {"permissions": list(optional_permissions), "origins": [], "data_collection": []}}, f)
    if load in ("profile-xpi", "profile-dir"):
        exts = os.path.join(profile, "extensions")
        os.makedirs(exts, exist_ok=True)
        if load == "profile-xpi":
            xpi = xpi or os.path.join(os.path.expanduser("~/.cache/bombadil-lab/build/bombadil-mail-lab.xpi"))
            if not os.path.exists(xpi):
                import subprocess
                subprocess.check_call([sys.executable, os.path.join(HERE, "build_xpi.py"), xpi], stdout=subprocess.DEVNULL)
            shutil.copy(xpi, os.path.join(exts, EXT_ID + ".xpi"))
        else:
            shutil.copytree(os.path.join(HERE, "extension"), os.path.join(exts, EXT_ID))
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("profile")
    ap.add_argument("--kind", default="lab")
    ap.add_argument("--email", default="test@example.test")
    ap.add_argument("--password", default="lab")
    ap.add_argument("--imap-port", type=int, default=1143)
    ap.add_argument("--smtp-port", type=int, default=1025)
    ap.add_argument("--load", default="profile-xpi")
    ap.add_argument("--xpi")
    ap.add_argument("--nss-dir")
    ap.add_argument("--extra-user-js")
    a = ap.parse_args()
    write_profile(a.profile, a.kind, a.email, a.password, imap_port=a.imap_port, smtp_port=a.smtp_port,
                  load=a.load, xpi=a.xpi, nss_dir=a.nss_dir, extra_user_js=a.extra_user_js)
    print(a.profile)


if __name__ == "__main__":
    main()

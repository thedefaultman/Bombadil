"""mail/engine.py: the profile Thunderbird starts with, the program it runs as, and the window it is shown in.

Everything here runs against `tests/mail/fake_thunderbird.py`, a stand-in that records how it was started, holds the
profile's lock the way Thunderbird does and quits on SIGTERM; the home, the profile, the state and the runtime
directories are in the test's temp dir (BOMBADIL_* and HOME), nothing touches the network, and a fixture stops
every Thunderbird (fake or real) a test left running, whole process group included. The last test needs a real
Thunderbird and a Dovecot, and skips cleanly without them.

What the tests hold the engine to, in the order of the file: the prefs it writes for an account are exactly what
Thunderbird's own setup would write (goldens, one per kind of provider), seeding twice changes nothing and
forgetting is for good in user.js, prefs.js, logins.json and the mail folder; the add-on is a reproducible .xpi
that is rebuilt when its source changed; the native-messaging manifest names the host by absolute path; a start
passes the flags and the environment it should, in a process group of its own, and a stop, a crash, a stale lock
and a Thunderbird left by an earlier run are all seen for what they are; Thunderbird's windows are picked out of
Hyprland's own JSON and shown or put away with the dispatchers the repo already uses; and the files are only ever
written where they are meant to be, whatever an address, a name or a registry says.
"""

import contextlib
import inspect
import json
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import pytest

from bombadil import paths
from bombadil.mail import accounts as accts
from bombadil.mail import engine, fake
from bombadil.mail.engine import ThunderbirdProcess

REPO = Path(__file__).resolve().parents[1]
FAKE = REPO / "tests" / "mail" / "fake_thunderbird.py"
LAB = REPO / "tests" / "mail" / "lab"

GOLDEN_GOOGLE = """\
// ---- account a1 (google)
user_pref("mail.account.account2.server", "server2");
user_pref("mail.account.account2.identities", "id2");
user_pref("mail.server.server2.type", "imap");
user_pref("mail.server.server2.hostname", "imap.gmail.com");
user_pref("mail.server.server2.port", 993);
user_pref("mail.server.server2.userName", "maya@acme.example");
user_pref("mail.server.server2.name", "maya@acme.example");
user_pref("mail.server.server2.socketType", 3);
user_pref("mail.server.server2.authMethod", 10);
user_pref("mail.server.server2.login_at_startup", true);
user_pref("mail.server.server2.check_new_mail", true);
user_pref("mail.server.server2.check_time", 1);
user_pref("mail.server.server2.use_idle", true);
user_pref("mail.server.server2.valid", true);
user_pref("mail.server.server2.delete_model", 1);
user_pref("mail.server.server2.directory-rel", "[ProfD]ImapMail/imap.gmail.com-2");
user_pref("mail.server.server2.autosync_offline_stores", true);
user_pref("mail.server.server2.offline_download", true);
user_pref("mail.server.server2.max_cached_connections", 2);
user_pref("mail.identity.id2.fullName", "Maya Okafor");
user_pref("mail.identity.id2.useremail", "maya@acme.example");
user_pref("mail.identity.id2.smtpServer", "smtp2");
user_pref("mail.identity.id2.valid", true);
user_pref("mail.identity.id2.compose_html", false);
user_pref("mail.identity.id2.reply_on_top", 1);
user_pref("mail.identity.id2.archive_granularity", 0);
user_pref("mail.identity.id2.fcc", false);
user_pref("mail.smtpserver.smtp2.hostname", "smtp.gmail.com");
user_pref("mail.smtpserver.smtp2.port", 465);
user_pref("mail.smtpserver.smtp2.username", "maya@acme.example");
user_pref("mail.smtpserver.smtp2.try_ssl", 3);
user_pref("mail.smtpserver.smtp2.authMethod", 10);
user_pref("mail.smtpserver.smtp2.description", "maya@acme.example");
"""

GOLDEN_MICROSOFT = """\
// ---- account a1 (microsoft)
user_pref("mail.account.account2.server", "server2");
user_pref("mail.account.account2.identities", "id2");
user_pref("mail.server.server2.type", "imap");
user_pref("mail.server.server2.hostname", "outlook.office365.com");
user_pref("mail.server.server2.port", 993);
user_pref("mail.server.server2.userName", "sam@contoso.example");
user_pref("mail.server.server2.name", "sam@contoso.example");
user_pref("mail.server.server2.socketType", 3);
user_pref("mail.server.server2.authMethod", 10);
user_pref("mail.server.server2.login_at_startup", true);
user_pref("mail.server.server2.check_new_mail", true);
user_pref("mail.server.server2.check_time", 1);
user_pref("mail.server.server2.use_idle", true);
user_pref("mail.server.server2.valid", true);
user_pref("mail.server.server2.delete_model", 1);
user_pref("mail.server.server2.directory-rel", "[ProfD]ImapMail/outlook.office365.com-2");
user_pref("mail.server.server2.autosync_offline_stores", true);
user_pref("mail.server.server2.offline_download", true);
user_pref("mail.server.server2.max_cached_connections", 2);
user_pref("mail.identity.id2.fullName", "Sam Reyes");
user_pref("mail.identity.id2.useremail", "sam@contoso.example");
user_pref("mail.identity.id2.smtpServer", "smtp2");
user_pref("mail.identity.id2.valid", true);
user_pref("mail.identity.id2.compose_html", false);
user_pref("mail.identity.id2.reply_on_top", 1);
user_pref("mail.identity.id2.archive_granularity", 0);
user_pref("mail.identity.id2.fcc", true);
user_pref("mail.smtpserver.smtp2.hostname", "smtp.office365.com");
user_pref("mail.smtpserver.smtp2.port", 587);
user_pref("mail.smtpserver.smtp2.username", "sam@contoso.example");
user_pref("mail.smtpserver.smtp2.try_ssl", 2);
user_pref("mail.smtpserver.smtp2.authMethod", 10);
user_pref("mail.smtpserver.smtp2.description", "sam@contoso.example");
"""

GOLDEN_ICLOUD = """\
// ---- account a1 (icloud)
user_pref("mail.account.account2.server", "server2");
user_pref("mail.account.account2.identities", "id2");
user_pref("mail.server.server2.type", "imap");
user_pref("mail.server.server2.hostname", "imap.mail.me.com");
user_pref("mail.server.server2.port", 993);
user_pref("mail.server.server2.userName", "kim");
user_pref("mail.server.server2.name", "kim@icloud.example");
user_pref("mail.server.server2.socketType", 3);
user_pref("mail.server.server2.authMethod", 3);
user_pref("mail.server.server2.login_at_startup", true);
user_pref("mail.server.server2.check_new_mail", true);
user_pref("mail.server.server2.check_time", 1);
user_pref("mail.server.server2.use_idle", true);
user_pref("mail.server.server2.valid", true);
user_pref("mail.server.server2.delete_model", 1);
user_pref("mail.server.server2.directory-rel", "[ProfD]ImapMail/imap.mail.me.com-2");
user_pref("mail.server.server2.autosync_offline_stores", true);
user_pref("mail.server.server2.offline_download", true);
user_pref("mail.server.server2.max_cached_connections", 2);
user_pref("mail.identity.id2.fullName", "");
user_pref("mail.identity.id2.useremail", "kim@icloud.example");
user_pref("mail.identity.id2.smtpServer", "smtp2");
user_pref("mail.identity.id2.valid", true);
user_pref("mail.identity.id2.compose_html", false);
user_pref("mail.identity.id2.reply_on_top", 1);
user_pref("mail.identity.id2.archive_granularity", 0);
user_pref("mail.identity.id2.fcc", true);
user_pref("mail.smtpserver.smtp2.hostname", "smtp.mail.me.com");
user_pref("mail.smtpserver.smtp2.port", 587);
user_pref("mail.smtpserver.smtp2.username", "kim@icloud.example");
user_pref("mail.smtpserver.smtp2.try_ssl", 2);
user_pref("mail.smtpserver.smtp2.authMethod", 3);
user_pref("mail.smtpserver.smtp2.description", "kim@icloud.example");
"""

GOLDEN_IMAP = """\
// ---- account a1 (imap)
user_pref("mail.account.account2.server", "server2");
user_pref("mail.account.account2.identities", "id2");
user_pref("mail.server.server2.type", "imap");
user_pref("mail.server.server2.hostname", "imap.school.example");
user_pref("mail.server.server2.port", 993);
user_pref("mail.server.server2.userName", "lee@school.example");
user_pref("mail.server.server2.name", "Lee's school");
user_pref("mail.server.server2.socketType", 3);
user_pref("mail.server.server2.authMethod", 3);
user_pref("mail.server.server2.login_at_startup", true);
user_pref("mail.server.server2.check_new_mail", true);
user_pref("mail.server.server2.check_time", 1);
user_pref("mail.server.server2.use_idle", true);
user_pref("mail.server.server2.valid", true);
user_pref("mail.server.server2.delete_model", 1);
user_pref("mail.server.server2.directory-rel", "[ProfD]ImapMail/imap.school.example-2");
user_pref("mail.server.server2.autosync_offline_stores", true);
user_pref("mail.server.server2.offline_download", true);
user_pref("mail.server.server2.max_cached_connections", 2);
user_pref("mail.identity.id2.fullName", "Lee Park");
user_pref("mail.identity.id2.useremail", "lee@school.example");
user_pref("mail.identity.id2.smtpServer", "smtp2");
user_pref("mail.identity.id2.valid", true);
user_pref("mail.identity.id2.compose_html", false);
user_pref("mail.identity.id2.reply_on_top", 1);
user_pref("mail.identity.id2.archive_granularity", 0);
user_pref("mail.identity.id2.fcc", true);
user_pref("mail.smtpserver.smtp2.hostname", "smtp.school.example");
user_pref("mail.smtpserver.smtp2.port", 587);
user_pref("mail.smtpserver.smtp2.username", "lee@school.example");
user_pref("mail.smtpserver.smtp2.try_ssl", 2);
user_pref("mail.smtpserver.smtp2.authMethod", 3);
user_pref("mail.smtpserver.smtp2.description", "lee@school.example");
"""

GOLDEN_HEAD = """\
// Written by bombadil-mail (bombadil/mail/engine.py) before every start. Thunderbird reads this file at
// each start, so what is changed here by hand is lost; what it learns by itself is in prefs.js.

// ---- quiet
user_pref("mail.provider.suppress_dialog_on_startup", true);
user_pref("mail.shell.checkDefaultClient", false);
user_pref("mail.shell.checkDefaultCalendar", false);
user_pref("mailnews.start_page.enabled", false);
user_pref("mail.rights.version", 1);
user_pref("mail.chat.enabled", false);
user_pref("mail.inappnotifications.enabled", false);
user_pref("mail.biff.show_alert", false);
user_pref("mail.biff.play_sound", false);
user_pref("mail.biff.use_system_alert", false);
user_pref("mail.biff.show_tray_icon_always", false);
user_pref("mail.biff.show_badge", false);
user_pref("app.update.enabled", false);
user_pref("app.update.auto", false);
user_pref("app.update.staging.enabled", false);
user_pref("app.update.checkInstallTime", false);
user_pref("extensions.update.enabled", false);
user_pref("extensions.update.autoUpdateDefault", false);
user_pref("extensions.blocklist.enabled", false);
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("datareporting.healthreport.uploadEnabled", false);
user_pref("toolkit.telemetry.enabled", false);
user_pref("toolkit.telemetry.unified", false);
user_pref("toolkit.telemetry.archive.enabled", false);
user_pref("toolkit.telemetry.server", "");
user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);
user_pref("datareporting.policy.firstRunURL", "");
user_pref("browser.crashReports.unsubmittedCheck.enabled", false);
user_pref("network.captive-portal-service.enabled", false);
user_pref("network.connectivity-service.enabled", false);
user_pref("browser.safebrowsing.malware.enabled", false);
user_pref("browser.safebrowsing.phishing.enabled", false);
user_pref("mail.server.default.check_new_mail", true);
user_pref("mailnews.message_warning_size", 0);
user_pref("mail.compose.attachment_reminder", false);
user_pref("mail.compose.attachment_reminder_aggressive", false);
user_pref("mail.SpellCheckBeforeSend", false);
user_pref("mail.compose.warn_public_recipients.aggressive", false);
user_pref("mail.compose.autosave", false);
user_pref("offline.send.unsent_messages", 2);
user_pref("offline.startup_state", 0);

// ---- add-on
user_pref("extensions.autoDisableScopes", 0);
user_pref("extensions.enabledScopes", 15);
user_pref("extensions.startupScanScopes", 15);
user_pref("xpinstall.signatures.required", false);

"""

GOLDEN_ACCOUNTS_NONE = """\
// ---- accounts
user_pref("mail.accountmanager.accounts", "account1");
user_pref("mail.accountmanager.localfoldersserver", "server1");

"""

GOLDEN_ACCOUNTS_ONE = """\
// ---- accounts
user_pref("mail.accountmanager.accounts", "account1,account2");
user_pref("mail.accountmanager.localfoldersserver", "server1");
user_pref("mail.accountmanager.defaultaccount", "account2");
user_pref("mail.smtpservers", "smtp2");
user_pref("mail.smtp.defaultserver", "smtp2");

"""

GOLDEN_LOCAL_FOLDERS = """\
// ---- local folders
user_pref("mail.account.account1.server", "server1");
user_pref("mail.server.server1.type", "none");
user_pref("mail.server.server1.hostname", "Local Folders");
user_pref("mail.server.server1.userName", "nobody");
user_pref("mail.server.server1.name", "Local Folders");
user_pref("mail.server.server1.directory-rel", "[ProfD]Mail/Local Folders");
user_pref("mail.server.server1.valid", true);
"""

# -- the world a test runs in --


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Home, profile, state and runtime in the temp dir; no display, no Hyprland, none of the person's Mozilla
    switches, nobody else's Thunderbird."""
    for var in list(os.environ):
        if var.startswith(("MOZ_", "BOMBADIL_FAKE")) or var in ("DISPLAY", "WAYLAND_DISPLAY", "BOMBADIL_MAIL_THUNDERBIRD",
                                                                  "HYPRLAND_INSTANCE_SIGNATURE"):
            monkeypatch.delenv(var)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "xdg-run"))
    monkeypatch.setenv("BOMBADIL_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("BOMBADIL_RUNTIME", str(tmp_path / "run"))
    monkeypatch.setenv("BOMBADIL_MAIL_PROFILE", str(tmp_path / "profile"))
    (tmp_path / "xdg-run").mkdir()
    (tmp_path / "home").mkdir()
    return tmp_path


def write_addon(directory: Path, marker: str = "one", optional=("messages.send",), addon_id=engine.ADDON_ID) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {"manifest_version": 2, "name": "Bombadil Mail", "version": "0.1.0",
                "browser_specific_settings": {"gecko": {"id": addon_id}},
                "permissions": ["nativeMessaging"], "background": {"scripts": ["background.js"], "persistent": True}}
    if optional:
        manifest["optional_permissions"] = list(optional)
    (directory / "manifest.json").write_text(json.dumps(manifest))
    (directory / "background.js").write_text(f"// {marker}\n")
    return directory


@pytest.fixture
def addon(world):
    return write_addon(world / "addon")


@pytest.fixture
def host(world):
    path = world / "bin" / "bombadil-mail-host"
    path.parent.mkdir(exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def fake_tb(world):
    """An executable called `thunderbird` that runs the fake under that name, as the real one's command line shows it."""
    path = world / "bin" / "thunderbird"
    path.parent.mkdir(exist_ok=True)
    path.write_text(f'#!/bin/bash\nexec -a thunderbird "{sys.executable}" "{FAKE}" "$@"\n')
    path.chmod(0o755)
    return path


@pytest.fixture
def make(world, addon, host, fake_tb, request):
    """ThunderbirdProcess objects on the fake, each stopped (its group killed) when the test is over."""
    made: list[ThunderbirdProcess] = []

    def build(**kw) -> ThunderbirdProcess:
        kw = {"binary": str(fake_tb), "addon_dir": addon, "host": host, "stop_timeout": 2.0, **kw}
        p = ThunderbirdProcess(**kw)
        made.append(p)
        return p

    def cleanup():
        for p in made:
            with contextlib.suppress(Exception):
                p.stop()
        for run in fake_runs(paths.mail_profile()):
            for pid in (run["pid"], run["child"]):
                if pid:
                    with contextlib.suppress(OSError):
                        os.kill(pid, signal.SIGKILL)

    request.addfinalizer(cleanup)
    return build


@pytest.fixture
def tb(make):
    return make()


@pytest.fixture
def windowed(world, monkeypatch):
    """A display that cannot be checked from here ("host:n"), so a Thunderbird started now gets a window."""
    monkeypatch.setenv("DISPLAY", "somewhere.example:3")


def row(n=1, email="maya@acme.example", sender="Maya Okafor", name="") -> dict:
    return {"id": f"a{n}", "email": email, "name": name, "sender": sender, "provider": "google"}


def fake_runs(profile: Path) -> list[dict]:
    """What each fake Thunderbird that started on the profile recorded, oldest first."""
    runs = [json.loads(p.read_text()) for p in profile.glob("fake-thunderbird-*.json")]
    return sorted(runs, key=lambda r: r["started"])


def wait_for(cond, what="", timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = cond()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what or cond}")


def gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    try:
        return Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()[0] == "Z"
    except OSError:
        return True


def user_js(profile: Path) -> str:
    return (profile / "user.js").read_text()


def section(text: str, title: str) -> str:
    """The part of user.js that starts at `// ---- title` and goes to the blank line after it."""
    start = text.index(f"// ---- {title}")
    return text[start:text.index("\n\n", start) + 1] if "\n\n" in text[start:] else text[start:]


# -- the interface the service calls --


def test_it_has_the_interface_the_service_calls_and_the_fake_has():
    for name, member in inspect.getmembers(fake.FakeProcess, inspect.isfunction):
        if name.startswith("_") or name in ("crash",):
            continue
        real = getattr(ThunderbirdProcess, name, None)
        assert real is not None, f"ThunderbirdProcess has no {name}"
        ours = list(inspect.signature(real).parameters)[1:]
        theirs = list(inspect.signature(member).parameters)[1:]
        assert ours == theirs, f"{name}: {ours} is not {theirs}"


def test_it_is_made_with_nothing_the_way_the_service_makes_it(world):
    p = ThunderbirdProcess()
    assert p.profile == world / "profile"
    assert p.addon_dir() == REPO / "share" / "mail" / "extension"
    assert p.host_path() == REPO / "bin" / "bombadil-mail-host"
    assert p.manifest_path() == world / "home" / ".mozilla" / "native-messaging-hosts" / "bombadil_mail.json"


def test_available_says_why_not_in_one_sentence(make, world, monkeypatch, tmp_path):
    assert make().available() == (True, "")
    ok, why = make(binary=str(world / "nothing-here")).available()
    assert not ok and why.endswith("so mail cannot be fetched.") and "\n" not in why
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert make(binary=None).available()[1] == why, "no thunderbird on PATH is the same sentence"
    ok, why = make(addon_dir=world / "no-addon").available()
    assert not ok and "add-on" in why
    ok, why = make(host=world / "no-host").available()
    assert not ok and "bombadil-mail-host" in why


def test_the_binary_can_be_named_in_the_environment(make, fake_tb, monkeypatch):
    monkeypatch.setenv("BOMBADIL_MAIL_THUNDERBIRD", str(fake_tb))
    assert make(binary=None).binary() == str(fake_tb)
    monkeypatch.setenv("BOMBADIL_MAIL_THUNDERBIRD", "/nonexistent/thunderbird")
    assert make(binary=None).available()[0] is False


# -- what a profile ends up with --


def test_prepare_makes_a_private_profile_with_everything_a_start_needs(make, world, addon, host):
    tb = make()
    tb.prepare()
    profile = world / "profile"
    assert stat.S_IMODE(profile.stat().st_mode) == 0o700
    assert (profile / "user.js").is_file() and stat.S_IMODE((profile / "user.js").stat().st_mode) == 0o600
    xpi = profile / "extensions" / f"{engine.ADDON_ID}.xpi"
    assert zipfile.ZipFile(xpi).namelist() == ["background.js", "manifest.json"]
    manifest = json.loads(tb.manifest_path().read_text())
    assert manifest == {"name": "bombadil_mail", "description": manifest["description"], "path": str(host),
                        "type": "stdio", "allowed_extensions": [engine.ADDON_ID]}
    assert stat.S_IMODE(tb.manifest_path().stat().st_mode) == 0o644


def test_prepare_refuses_what_cannot_start_and_says_why(make, world):
    with pytest.raises(RuntimeError, match="not installed"):
        make(binary=str(world / "none")).prepare()
    assert not (world / "profile" / "user.js").exists()


def test_prepare_twice_rewrites_nothing(make, world):
    tb = make()
    tb.seed_account(row(), accts.GOOGLE)
    tb.prepare()
    files = [world / "profile" / "user.js", world / "profile" / "bombadil-engine.json",
             world / "profile" / "extension-preferences.json", tb.manifest_path(),
             world / "profile" / "extensions" / f"{engine.ADDON_ID}.xpi"]
    before = [(f.read_bytes(), f.stat().st_mtime_ns, f.stat().st_ino) for f in files]
    tb.prepare()
    tb.seed_account(row(), accts.GOOGLE)
    tb.prepare()
    assert [(f.read_bytes(), f.stat().st_mtime_ns, f.stat().st_ino) for f in files] == before


def test_a_hand_made_change_to_user_js_is_put_back_by_the_next_prepare(make, world):
    tb = make()
    tb.prepare()
    good = user_js(world / "profile")
    (world / "profile" / "user.js").write_text("user_pref('mail.chat.enabled', true);\n")
    tb.prepare()
    assert user_js(world / "profile") == good


QUIET_MUST = {
    # the windows that must never appear
    "mail.provider.suppress_dialog_on_startup": "true", "mail.shell.checkDefaultClient": "false",
    "mailnews.start_page.enabled": "false", "mail.rights.version": "1", "mail.chat.enabled": "false",
    # nothing phones home
    "app.update.enabled": "false", "toolkit.telemetry.enabled": "false", "datareporting.policy.dataSubmissionEnabled": "false",
    "extensions.blocklist.enabled": "false", "network.captive-portal-service.enabled": "false",
    # the modal dialogs of a send
    "mailnews.message_warning_size": "0", "mail.compose.attachment_reminder_aggressive": "false",
    "mail.SpellCheckBeforeSend": "false", "mail.compose.autosave": "false",
    # nothing is sent but by a press, and it starts online
    "offline.send.unsent_messages": "2", "offline.startup_state": "0",
    # loading the add-on
    "extensions.autoDisableScopes": "0", "extensions.startupScanScopes": "15", "xpinstall.signatures.required": "false",
}


def test_user_js_has_the_quiet_prefs_the_lab_found_necessary_and_nothing_only_the_lab_needed(make, world):
    make().prepare()
    text = user_js(world / "profile")
    lines = {ln.split('"')[1]: ln.rpartition(", ")[2].removesuffix(");") for ln in text.splitlines()
             if ln.startswith("user_pref(")}
    for name, value in QUIET_MUST.items():
        assert lines.get(name) == value, name
    for lab_only in ("network.proxy.type", "general.useragent.locale", "devtools.console.stdout.chrome",
                     "extensions.logging.enabled", "browser.dom.window.dump.enabled"):
        assert lab_only not in lines, lab_only
    assert all(ln.startswith(("user_pref(", "//")) or not ln for ln in text.splitlines()), "nothing but prefs"


def test_user_js_with_no_account_is_exactly_the_quiet_prefs_the_add_on_and_local_folders(make, world):
    make().prepare()
    assert user_js(world / "profile") == GOLDEN_HEAD + GOLDEN_ACCOUNTS_NONE + GOLDEN_LOCAL_FOLDERS


def test_user_js_with_an_account_is_exactly_that_with_the_accounts_list_and_the_account(make, world):
    make().seed_account(row(1), accts.GOOGLE)
    assert user_js(world / "profile") == (GOLDEN_HEAD + GOLDEN_ACCOUNTS_ONE + GOLDEN_LOCAL_FOLDERS + "\n"
                                          + GOLDEN_GOOGLE)


@pytest.mark.parametrize("n, email, sender, name, provider, golden", [
    (1, "maya@acme.example", "Maya Okafor", "", accts.GOOGLE, GOLDEN_GOOGLE),
    (1, "sam@contoso.example", "Sam Reyes", "", accts.MICROSOFT, GOLDEN_MICROSOFT),
    (1, "kim@icloud.example", "", "", accts.ICLOUD, GOLDEN_ICLOUD),
    (1, "lee@school.example", "Lee Park", "Lee's school", accts.IMAP, GOLDEN_IMAP),
], ids=["google", "microsoft", "icloud", "generic imap"])
def test_a_seeded_account_is_exactly_what_thunderbirds_own_setup_writes(make, world, n, email, sender, name, provider,
                                                                        golden):
    """The service hands over `accts.IMAP` as it is, with `imap.{domain}` still in it: the engine makes the host."""
    make().seed_account(row(n, email, sender, name), provider)
    text = user_js(world / "profile")
    assert section(text, f"account a{n}") == golden
    assert 'user_pref("mail.accountmanager.accounts", "account1,account2");' in text


def test_oauth_accounts_use_oauth_and_password_accounts_a_password_for_both_servers(make, world):
    tb = make()
    tb.seed_account(row(1, "a@acme.example"), accts.GOOGLE)
    tb.seed_account(row(2, "b@contoso.example"), accts.MICROSOFT)
    tb.seed_account(row(3, "c@icloud.example"), accts.ICLOUD)
    tb.seed_account(row(4, "d@school.example"), accts.IMAP)
    text = user_js(world / "profile")
    for n, method in ((2, 10), (3, 10), (4, 3), (5, 3)):
        assert f'"mail.server.server{n}.authMethod", {method});' in text
        assert f'"mail.smtpserver.smtp{n}.authMethod", {method});' in text
    assert 'user_pref("mail.accountmanager.accounts", "account1,account2,account3,account4,account5");' in text
    assert 'user_pref("mail.smtpservers", "smtp2,smtp3,smtp4,smtp5");' in text
    assert 'user_pref("mail.accountmanager.defaultaccount", "account2");' in text


def test_every_account_archives_flat_and_nothing_stores_a_password(make, world):
    tb = make()
    tb.seed_account(row(1), accts.GOOGLE)
    tb.seed_account(row(2, "k@icloud.example"), accts.ICLOUD)
    text = user_js(world / "profile")
    assert text.count("archive_granularity\", 0);") == 2
    assert "password" not in text.lower().replace("password-", "") or "passwordCleartext" not in text
    assert not any((world / "profile").glob("logins*")), "a password is Thunderbird's own to keep"


def test_seeding_the_same_address_twice_changes_nothing_and_another_id_for_it_replaces_the_first(make, world):
    profile = world / "profile"
    tb = make()
    tb.seed_account(row(1), accts.GOOGLE)
    once = (user_js(profile), (profile / "bombadil-engine.json").read_bytes())
    tb.seed_account(row(1), accts.GOOGLE)
    assert (user_js(profile), (profile / "bombadil-engine.json").read_bytes()) == once
    (profile / "ImapMail" / "imap.gmail.com-2").mkdir(parents=True)     # what the first one's sync made
    tb.seed_account(row(7), accts.GOOGLE)    # the same address under a new id: the old number goes
    text = user_js(profile)
    assert 'user_pref("mail.accountmanager.accounts", "account1,account8");' in text
    assert text.count('"mail.identity.id8.useremail"') == 1 and "id2." not in text and "server2." not in text
    assert not (profile / "ImapMail" / "imap.gmail.com-2").exists(), "the replaced account's mail goes with it"


def test_the_generic_provider_is_made_for_the_domain_even_when_handed_over_as_a_template(make, world):
    make().seed_account(row(1, "lee@Mail.School.Example"), accts.IMAP)
    text = user_js(world / "profile")
    assert '"mail.server.server2.hostname", "imap.mail.school.example"' in text
    assert '"mail.smtpserver.smtp2.hostname", "smtp.mail.school.example"' in text
    assert '"mail.server.server2.userName", "lee@mail.school.example"' in text


def test_a_name_with_quotes_tabs_and_line_breaks_stays_inside_its_pref(make, world):
    nasty = 'Maya "the \\ boss"\t\n// user_pref("mail.chat.enabled", true);   \x07 end'
    make().seed_account(row(1, sender=nasty, name=nasty), accts.GOOGLE)
    text = user_js(world / "profile")
    assert all(ln.startswith(("user_pref(", "//")) or not ln for ln in text.splitlines())
    full = [ln for ln in text.splitlines() if ln.startswith('user_pref("mail.identity.id2.fullName"')]
    assert len(full) == 1 and '\\"the \\\\ boss\\"' in full[0] and "\\t" not in full[0] and "\\u0007" not in full[0]
    assert text.count('user_pref("mail.chat.enabled"') == 1


def test_the_prefs_of_an_account_with_a_name_that_is_not_ascii_are_ascii(make, world):
    make().seed_account(row(1, sender="Zoë Müller 日本 \U0001F600"), accts.GOOGLE)
    line = next(ln for ln in user_js(world / "profile").splitlines() if "id2.fullName" in ln)
    assert line.isascii() and json.loads(line[line.index(", ") + 2:-2]) == "Zoë Müller 日本 \U0001F600"


@pytest.mark.parametrize("bad", [
    {"id": "a0"}, {"id": "b1"}, {"id": "../a1"}, {"id": "a1/../../x"}, {"id": "a99999999999"}, {"id": None},
    {"email": "not an address"}, {"email": "a@b"},
])
def test_an_account_that_could_not_be_a_pref_name_or_an_address_is_refused_and_nothing_is_written(make, world, bad):
    with pytest.raises(ValueError):
        make().seed_account({**row(), **bad}, accts.GOOGLE)
    assert not (world / "profile" / "user.js").exists()


@pytest.mark.parametrize("field, value", [
    ("imap_host", "../../victim"), ("imap_host", "imap.x/../../y.example"), ("imap_host", "a b.example"),
    ("imap_host", ""), ("imap_host", "\u00e9" * 70 + ".example"), ("imap_host", 'x".example'), ("smtp_host", "smtp.example/../.."), ("imap_port", 0),
    ("imap_port", 70000), ("imap_port", True), ("imap_port", "993"), ("imap_security", "plain"),
    ("imap_security", "none"), ("smtp_security", "none"), ("auth", "magic"),
])
def test_a_provider_that_is_not_a_server_is_refused(make, world, field, value):
    from dataclasses import replace
    with pytest.raises(ValueError):
        make().seed_account(row(), replace(accts.GOOGLE, **{field: value}))
    assert not (world / "profile" / "bombadil-engine.json").exists()


def test_no_encryption_is_only_for_a_server_on_this_computer(make, world):
    from dataclasses import replace
    here = replace(accts.IMAP, imap_host="127.0.0.1", imap_port=1143, imap_security="none", smtp_host="localhost",
                   smtp_port=1025, smtp_security="none")
    make().seed_account(row(), here)
    assert '"mail.server.server2.socketType", 0);' in user_js(world / "profile")
    with pytest.raises(ValueError, match="in the clear"):
        make().seed_account(row(2, "b@school.example"), replace(here, imap_host="mail.school.example"))


def test_a_server_name_in_unicode_is_made_ascii_and_still_a_folder_name_that_is_safe(make, world):
    from dataclasses import replace
    make().seed_account(row(), replace(accts.IMAP, imap_host="imap.münchen.example"))
    text = user_js(world / "profile")
    assert '"mail.server.server2.hostname", "imap.xn--mnchen-3ya.example"' in text
    assert '"[ProfD]ImapMail/imap.xn--mnchen-3ya.example-2"' in text


def test_a_registry_that_was_tampered_with_cannot_put_anything_into_user_js(make, world):
    tb = make()
    tb.seed_account(row(1), accts.GOOGLE)
    path = world / "profile" / "bombadil-engine.json"
    data = json.loads(path.read_text())
    evil = json.loads(json.dumps(data["accounts"]["a1"]))
    evil["imap"]["host"] = "x.example\"); user_pref(\"mail.chat.enabled\", true); //"
    evil["id"] = "a2"
    other = json.loads(json.dumps(data["accounts"]["a1"]))
    other["n"] = 5      # a number that is not the id's
    data["accounts"]["a2"] = evil
    data["accounts"]["a3"] = other
    data["accounts"]["a9"] = "not an account"
    path.write_text(json.dumps(data))
    tb.prepare()
    text = user_js(world / "profile")
    assert "chat.enabled\", true" not in text and "account5" not in text and "account10" not in text
    assert "account2" in text, "the good account stays"
    path.write_text("{ not json")
    tb.prepare()
    assert "id2.useremail" not in user_js(world / "profile"), "a registry that cannot be read is no accounts"
    for odd in ('{"accounts": [1, 2]}', '[]', '{"accounts": {}, "forgotten": "x"}', "null"):
        path.write_text(odd)
        tb.prepare()
        assert "mail.identity.id" not in user_js(world / "profile"), odd


def test_user_js_is_put_back_to_private_when_only_its_mode_was_changed(make, world):
    tb = make()
    tb.prepare()
    path = world / "profile" / "user.js"
    path.chmod(0o644)
    tb.prepare()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_file_that_cannot_be_written_is_an_error_and_leaves_no_temp_file_behind(world, tmp_path):
    work = tmp_path / "work"
    target = work / "in-the-way"
    target.mkdir(parents=True)
    with pytest.raises(OSError):
        engine._write(target, b"data", 0o600)
    src = write_addon(work / "src")
    with pytest.raises(OSError):
        engine.build_xpi(src, target)
    assert sorted(p.name for p in work.iterdir()) == ["in-the-way", "src"]


def test_an_account_whose_server_changed_leaves_its_old_folder_to_be_cleared(make, world):
    from dataclasses import replace
    tb = make()
    tb.seed_account(row(1, "lee@school.example"), accts.IMAP)
    (world / "profile" / "ImapMail" / "imap.school.example-2").mkdir(parents=True)
    tb.seed_account(row(1, "lee@school.example"), replace(accts.IMAP, imap_host="mail.school.example"))
    assert not (world / "profile" / "ImapMail" / "imap.school.example-2").exists()
    assert '"[ProfD]ImapMail/mail.school.example-2"' in user_js(world / "profile")


# -- forgetting an account for good --

PREFS_JS = """// Mozilla User Preferences

// DO NOT EDIT THIS FILE.
user_pref("app.update.lastUpdateTime.xpi-signature-verification", 0);
user_pref("mail.account.account2.identities", "id2");
user_pref("mail.account.account2.server", "server2");
user_pref("mail.account.account3.identities", "id3");
user_pref("mail.account.account3.server", "server3");
user_pref("mail.account.account20.server", "server20");
user_pref("mail.accountmanager.accounts", "account1,account2,account3");
user_pref("mail.accountmanager.defaultaccount", "account2");
user_pref("mail.accountmanager.localfoldersserver", "server1");
user_pref("mail.identity.id2.archive_folder", "imap://maya%40acme.example@imap.gmail.com/[Gmail]/All Mail");
user_pref("mail.identity.id2.useremail", "maya@acme.example");
user_pref("mail.identity.id3.useremail", "kim@icloud.example");
user_pref("mail.identity.id20.useremail", "someone@else.example");
user_pref("mail.server.server1.hostname", "Local Folders");
user_pref("mail.server.server2.hostname", "imap.gmail.com");
user_pref("mail.server.server2.userName", "maya@acme.example");
user_pref("mail.server.server2.serverIDResponse", "(\\"name\\" \\"GImap\\")");
user_pref("mail.server.server3.hostname", "imap.mail.me.com");
user_pref("mail.server.server3.userName", "kim");
user_pref("mail.server.server20.userName", "someone@else.example");
user_pref("mail.smtp.defaultserver", "smtp2");
user_pref("mail.smtpserver.smtp2.username", "maya@acme.example");
user_pref("mail.smtpserver.smtp3.username", "kim@icloud.example");
user_pref("mail.smtpservers", "smtp2,smtp3");
user_pref("mailnews.tags.version", 2);
"""


PREFS_JS_WITHOUT_ACCOUNT_2 = """// Mozilla User Preferences

// DO NOT EDIT THIS FILE.
user_pref("app.update.lastUpdateTime.xpi-signature-verification", 0);
user_pref("mail.account.account3.identities", "id3");
user_pref("mail.account.account3.server", "server3");
user_pref("mail.account.account20.server", "server20");
user_pref("mail.identity.id3.useremail", "kim@icloud.example");
user_pref("mail.identity.id20.useremail", "someone@else.example");
user_pref("mail.server.server1.hostname", "Local Folders");
user_pref("mail.server.server3.hostname", "imap.mail.me.com");
user_pref("mail.server.server3.userName", "kim");
user_pref("mail.server.server20.userName", "someone@else.example");
user_pref("mail.smtpserver.smtp3.username", "kim@icloud.example");
user_pref("mailnews.tags.version", 2);
"""


def mentions(text: str, n: int) -> bool:
    """Does a pref file speak of account, server, identity or SMTP server number n (and not of 20 or 21 for n=2)?"""
    return re.search(rf"(?:account|server|id|smtp){n}(?![0-9])", text) is not None


def thunderbirds_own_files(profile: Path, *, imap_dirs=("imap.gmail.com-2", "imap.mail.me.com-3")) -> None:
    """What a Thunderbird that had run with two seeded accounts would have left: prefs.js, the mail folders, logins."""
    (profile / "prefs.js").write_text(PREFS_JS)
    os.chmod(profile / "prefs.js", 0o600)
    for d in imap_dirs:
        (profile / "ImapMail" / d / "Archive.sbd").mkdir(parents=True)
        (profile / "ImapMail" / d / "INBOX").write_text("not read by anyone\n")
    logins = [{"id": i, "hostname": host, "httpRealm": host, "encryptedUsername": "x", "encryptedPassword": "y"}
              for i, host in enumerate(["imap://imap.gmail.com", "smtp://smtp.gmail.com", "oauth://accounts.google.com",
                                        "imap://imap.mail.me.com", "smtp://smtp.mail.me.com", "https://other.example"])]
    (profile / "logins.json").write_text(json.dumps({"nextId": 9, "logins": logins, "version": 3}))


def login_hosts(profile: Path) -> list[str]:
    return [x["hostname"] for x in json.loads((profile / "logins.json").read_text())["logins"]]


@pytest.fixture
def two_accounts(make, world):
    tb = make()
    tb.seed_account(row(1), accts.GOOGLE)
    tb.seed_account(row(2, "kim@icloud.example", sender=""), accts.ICLOUD)
    thunderbirds_own_files(world / "profile")
    return tb


def test_forgetting_an_account_removes_it_from_user_js_prefs_js_logins_and_the_mail_folder(two_accounts, world):
    profile = world / "profile"
    two_accounts.forget_account(row(1))
    text = user_js(profile)
    assert "maya@acme.example" not in text and not mentions(text, 2)
    assert 'user_pref("mail.accountmanager.accounts", "account1,account3");' in text
    assert 'user_pref("mail.accountmanager.defaultaccount", "account3");' in text
    assert 'user_pref("mail.smtpservers", "smtp3");' in text and "kim@icloud.example" in text
    # byte for byte: what was Thunderbird's about account 2 is gone, account 20 (a prefix of nothing) and the rest stay
    assert (profile / "prefs.js").read_text() == PREFS_JS_WITHOUT_ACCOUNT_2
    assert stat.S_IMODE((profile / "prefs.js").stat().st_mode) == 0o600
    assert not (profile / "ImapMail" / "imap.gmail.com-2").exists()
    assert (profile / "ImapMail" / "imap.mail.me.com-3" / "INBOX").exists()
    assert login_hosts(profile) == ["imap://imap.mail.me.com", "smtp://smtp.mail.me.com", "https://other.example"]
    assert json.loads((profile / "bombadil-engine.json").read_text())["forgotten"] == [], "nothing left to do"


def test_forgetting_the_other_one_too_leaves_a_profile_with_no_account_in_it(two_accounts, world):
    profile = world / "profile"
    two_accounts.forget_account(row(1))
    two_accounts.forget_account({"id": "a2", "email": "kim@icloud.example"})
    assert 'user_pref("mail.accountmanager.accounts", "account1");' in user_js(profile)
    prefs = (profile / "prefs.js").read_text()
    assert "icloud" not in prefs and "mail.me.com" not in prefs and "gmail" not in prefs
    assert not list((profile / "ImapMail").iterdir())
    assert login_hosts(profile) == ["https://other.example"]


def test_forgetting_what_is_not_known_does_nothing(two_accounts, world):
    before = (user_js(world / "profile"), (world / "profile" / "prefs.js").read_text())
    two_accounts.forget_account(row(9, "nobody@acme.example"))
    assert (user_js(world / "profile"), (world / "profile" / "prefs.js").read_text()) == before


def test_a_login_that_another_account_at_the_same_server_uses_stays(make, world):
    tb = make()
    tb.seed_account(row(1, "a@school.example"), accts.IMAP)
    tb.seed_account(row(2, "b@school.example"), accts.IMAP)
    (world / "profile" / "logins.json").write_text(json.dumps({"logins": [
        {"hostname": "imap://imap.school.example"}, {"hostname": "smtp://smtp.school.example"}]}))
    tb.forget_account(row(1, "a@school.example"))
    assert login_hosts(world / "profile") == ["imap://imap.school.example", "smtp://smtp.school.example"]
    tb.forget_account(row(2, "b@school.example"))
    assert login_hosts(world / "profile") == []


@pytest.mark.parametrize("logins", [None, "{ not json", '{"logins": "x"}', "[]"])
def test_forgetting_with_no_usable_login_store_still_forgets_the_account(make, world, logins):
    tb = make()
    tb.seed_account(row(1), accts.GOOGLE)
    if logins is not None:
        (world / "profile" / "logins.json").write_text(logins)
    tb.forget_account(row(1))
    assert "maya@acme.example" not in user_js(world / "profile")
    assert json.loads((world / "profile" / "bombadil-engine.json").read_text())["forgotten"] == []


def test_what_could_not_be_cleared_is_tried_again_and_a_note_that_makes_no_sense_is_not(two_accounts, world):
    profile = world / "profile"
    (profile / "prefs.js").unlink()
    (profile / "prefs.js").mkdir()                       # a file that cannot be read as one
    two_accounts.forget_account(row(1))
    notes = json.loads((profile / "bombadil-engine.json").read_text())["forgotten"]
    assert [n["n"] for n in notes] == [2], "kept for the next time"
    (profile / "prefs.js").rmdir()
    (profile / "prefs.js").write_text(PREFS_JS)
    two_accounts.prepare()
    assert (profile / "prefs.js").read_text() == PREFS_JS_WITHOUT_ACCOUNT_2
    assert json.loads((profile / "bombadil-engine.json").read_text())["forgotten"] == []


def test_a_stop_that_cannot_tidy_is_still_a_stop(two_accounts, world, monkeypatch):
    two_accounts.start()
    wait_for(lambda: fake_runs(world / "profile"), "the fake to start")

    def broken(self):
        raise OSError("disk on fire")

    monkeypatch.setattr(ThunderbirdProcess, "_scrub", broken)
    two_accounts.stop()
    assert two_accounts.running() is False


def test_two_google_accounts_share_a_sign_in_page_and_it_goes_with_the_last(make, world):
    tb = make()
    tb.seed_account(row(1, "a@acme.example"), accts.GOOGLE)
    tb.seed_account(row(2, "b@acme.example"), accts.GOOGLE)
    (world / "profile" / "logins.json").write_text(json.dumps({"logins": [{"hostname": "oauth://accounts.google.com"}]}))
    tb.forget_account(row(1, "a@acme.example"))
    assert login_hosts(world / "profile") == ["oauth://accounts.google.com"]
    tb.forget_account(row(2, "b@acme.example"))
    assert login_hosts(world / "profile") == []


def test_forgetting_while_thunderbird_runs_is_finished_when_it_stops(two_accounts, make, world):
    profile = world / "profile"
    tb = two_accounts
    tb.start()
    wait_for(lambda: fake_runs(profile), "the fake to start")
    tb.forget_account(row(1))
    assert "maya@acme.example" not in user_js(profile), "the next start does not load it, whatever else happens"
    assert "maya@acme.example" in (profile / "prefs.js").read_text(), "Thunderbird would only write it back"
    assert (profile / "ImapMail" / "imap.gmail.com-2").exists()
    assert json.loads((profile / "bombadil-engine.json").read_text())["forgotten"][0]["n"] == 2
    tb.stop()
    assert "maya@acme.example" not in (profile / "prefs.js").read_text()
    assert not (profile / "ImapMail" / "imap.gmail.com-2").exists()
    assert login_hosts(profile) == ["imap://imap.mail.me.com", "smtp://smtp.mail.me.com", "https://other.example"]


def test_what_comes_back_into_use_before_the_clearing_is_not_cleared(two_accounts, world):
    profile = world / "profile"
    tb = two_accounts
    tb.start()
    wait_for(lambda: fake_runs(profile), "the fake to start")
    tb.forget_account(row(1))
    tb.seed_account(row(5), accts.GOOGLE)      # the same address, added again while Thunderbird still runs
    tb.stop()
    assert "oauth://accounts.google.com" in login_hosts(profile) and "imap://imap.gmail.com" in login_hosts(profile)
    assert not (profile / "ImapMail" / "imap.gmail.com-2").exists(), "the old account's folder goes"
    (profile / "ImapMail" / "imap.gmail.com-6").mkdir(parents=True)
    tb.stop()
    assert (profile / "ImapMail" / "imap.gmail.com-6").exists(), "and the new one's does not"


def test_a_restart_clears_what_a_forget_left_while_it_ran(two_accounts, world):
    profile = world / "profile"
    tb = two_accounts
    tb.start()
    wait_for(lambda: fake_runs(profile), "the fake to start")
    tb.forget_account(row(1))
    tb.restart()
    assert "maya@acme.example" not in (profile / "prefs.js").read_text()
    assert len(wait_for(lambda: len(fake_runs(profile)) == 2 and fake_runs(profile), "a second start")) == 2


def test_the_clearing_cannot_leave_the_mail_folder_it_is_meant_for_nor_touch_a_live_one(two_accounts, world):
    profile = world / "profile"
    victim = world / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("mine")
    os.symlink(victim, profile / "ImapMail" / "imap.old.example-80")
    path = profile / "bombadil-engine.json"
    data = json.loads(path.read_text())
    data["forgotten"] = [{"n": 77, "dir": "../../victim", "logins": []}, {"n": 78, "dir": "x/../../victim", "logins": []},
                         {"n": 79, "dir": "..", "logins": []}, {"n": 80, "dir": "imap.old.example-80", "logins": []},
                         {"n": 81, "dir": "imap.mail.me.com-3", "logins": []},   # a live account's folder
                         {"n": 82, "dir": "/", "logins": ["https://other.example", "smtp://smtp.mail.me.com"]},
                         {"n": 1, "dir": "Local Folders-1", "logins": []}, "junk", {"dir": 5}]
    path.write_text(json.dumps(data))
    two_accounts.forget_account(row(9, "nobody@acme.example"))      # any change runs the clearing
    assert (victim / "keep.txt").read_text() == "mine"
    assert not (profile / "ImapMail" / "imap.old.example-80").is_symlink(), "a link is removed, never followed"
    assert (profile / "ImapMail" / "imap.mail.me.com-3" / "INBOX").exists(), "the folder of an account that is there"
    assert (profile / "ImapMail" / "imap.gmail.com-2" / "INBOX").exists()
    assert profile.is_dir() and (profile / "user.js").exists()
    assert len(login_hosts(profile)) == 6, "a note clears no login that is not mail's, nor one a live account uses"
    assert 'user_pref("mail.server.server1.hostname", "Local Folders");' in (profile / "prefs.js").read_text()
    assert json.loads(path.read_text())["forgotten"] == [], "every note is spent"


# -- the add-on --


def test_the_xpi_is_the_same_bytes_whenever_and_wherever_it_is_built(world, tmp_path):
    a = write_addon(tmp_path / "one")
    (a / "sub").mkdir()
    (a / "sub" / "x.js").write_text("// x\n")
    b = write_addon(tmp_path / "two")
    (b / "sub").mkdir()
    (b / "sub" / "x.js").write_text("// x\n")
    os.utime(b / "background.js", (1_000_000, 1_000_000))
    engine.build_xpi(a, tmp_path / "a.xpi")
    time.sleep(1.1)
    engine.build_xpi(b, tmp_path / "b.xpi")
    assert (tmp_path / "a.xpi").read_bytes() == (tmp_path / "b.xpi").read_bytes()
    z = zipfile.ZipFile(tmp_path / "a.xpi")
    assert z.namelist() == ["background.js", "manifest.json", "sub/x.js"]
    assert all(i.date_time == (1980, 1, 1, 0, 0, 0) and i.compress_type == zipfile.ZIP_STORED
               and (i.external_attr >> 16) == (stat.S_IFREG | 0o644) for i in z.infolist())
    assert z.comment.startswith(b"bombadil-source:") and len(z.comment) == len(b"bombadil-source:") + 64


def test_the_xpi_is_built_again_only_when_the_source_changed(world, tmp_path):
    src = write_addon(tmp_path / "src")
    xpi = tmp_path / "x.xpi"
    assert engine.build_xpi(src, xpi) is True
    stamp = xpi.stat().st_mtime_ns
    ino = xpi.stat().st_ino
    time.sleep(0.05)
    os.utime(src / "background.js")                      # touched, not changed
    assert engine.build_xpi(src, xpi) is False and xpi.stat().st_mtime_ns == stamp
    (src / "background.js").write_text("// two\n")
    assert engine.build_xpi(src, xpi) is True
    assert zipfile.ZipFile(xpi).read("background.js") == b"// two\n" and xpi.stat().st_ino != ino
    (src / "new.js").write_text("// a new file\n")
    assert engine.build_xpi(src, xpi) is True and "new.js" in zipfile.ZipFile(xpi).namelist()
    (src / "new.js").unlink()
    assert engine.build_xpi(src, xpi) is True and "new.js" not in zipfile.ZipFile(xpi).namelist()
    xpi.write_bytes(b"not a zip")
    assert engine.build_xpi(src, xpi) is True and zipfile.is_zipfile(xpi)
    xpi.unlink()
    assert engine.build_xpi(src, xpi) is True
    assert not list(tmp_path.glob(".*tmp")), "no temp file is left behind"


def test_what_is_hidden_or_a_cache_is_not_in_the_xpi(world, tmp_path):
    src = write_addon(tmp_path / "src")
    (src / ".git").mkdir()
    (src / ".git" / "config").write_text("x")
    (src / ".hidden.js").write_text("x")
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "x.pyc").write_bytes(b"x")
    os.symlink("/etc/passwd", src / "link.txt")
    engine.build_xpi(src, tmp_path / "x.xpi")
    assert zipfile.ZipFile(tmp_path / "x.xpi").namelist() == ["background.js", "manifest.json"]


def test_prepare_installs_a_changed_addon_and_leaves_an_unchanged_one(make, world, addon):
    tb = make()
    xpi = world / "profile" / "extensions" / f"{engine.ADDON_ID}.xpi"
    tb.prepare()
    first = xpi.stat().st_ino
    tb.prepare()
    assert xpi.stat().st_ino == first
    (addon / "background.js").write_text("// two\n")
    tb.prepare()
    assert zipfile.ZipFile(xpi).read("background.js") == b"// two\n"


def test_an_addon_with_another_id_than_the_native_manifest_allows_is_refused(make, world, tmp_path):
    other = write_addon(tmp_path / "other", addon_id="something-else@example.test")
    with pytest.raises(RuntimeError, match="id"):
        make(addon_dir=other).prepare()
    assert not (world / "profile" / "extensions" / f"{engine.ADDON_ID}.xpi").exists()
    (other / "manifest.json").write_text("{ not json")
    with pytest.raises(RuntimeError, match="cannot be read"):
        make(addon_dir=other).prepare()


def test_the_addons_optional_permissions_are_granted_without_a_prompt(make, world, addon):
    tb = make()
    path = world / "profile" / "extension-preferences.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"other@example.test": {"permissions": ["tabs"], "origins": [], "data_collection": []}}))
    tb.prepare()
    data = json.loads(path.read_text())
    assert data[engine.ADDON_ID] == {"permissions": ["messages.send"], "origins": [], "data_collection": []}
    assert data["other@example.test"]["permissions"] == ["tabs"], "somebody else's grants are left alone"
    stamp = path.stat().st_mtime_ns
    tb.prepare()
    assert path.stat().st_mtime_ns == stamp
    write_addon(addon, optional=())
    tb.prepare()
    assert engine.ADDON_ID not in json.loads(path.read_text()) and "other@example.test" in json.loads(path.read_text())


# -- the native-messaging manifest --


def test_the_manifest_names_the_host_by_absolute_path_and_is_written_only_when_it_differs(make, world, host):
    tb = make()
    tb.prepare()
    path = tb.manifest_path()
    assert path.parent == world / "home" / ".mozilla" / "native-messaging-hosts"
    data = json.loads(path.read_text())
    assert os.path.isabs(data["path"]) and data["path"] == str(host) and data["type"] == "stdio"
    assert data["name"] == "bombadil_mail" and data["allowed_extensions"] == ["bombadil-mail@bombadil.local"]
    stamp = path.stat().st_ino
    tb.prepare()
    assert path.stat().st_ino == stamp
    moved = world / "elsewhere" / "bombadil-mail-host"
    moved.parent.mkdir()
    moved.write_text("#!/bin/sh\n")
    moved.chmod(0o755)
    make(host=moved).prepare()
    assert json.loads(path.read_text())["path"] == str(moved)


def test_the_host_is_found_next_to_the_package_or_on_the_path(make, world, monkeypatch, tmp_path):
    p = ThunderbirdProcess()
    assert p.host_path() == REPO / "bin" / "bombadil-mail-host" and os.access(p.host_path(), os.X_OK)
    elsewhere = tmp_path / "onpath"
    elsewhere.mkdir()
    (elsewhere / "bombadil-mail-host").write_text("#!/bin/sh\n")
    (elsewhere / "bombadil-mail-host").chmod(0o755)
    monkeypatch.setenv("PATH", str(elsewhere))
    monkeypatch.setattr(engine.Path, "is_file", lambda self: False if self.name == "bombadil-mail-host" else
                        os.path.isfile(self))
    assert ThunderbirdProcess().host_path() == elsewhere / "bombadil-mail-host"
    assert make(host=tmp_path / "gone").host_path() is None


# -- starting, stopping, and what a start passes --


def argv_of(run) -> list[str]:
    return run["argv"]


def test_a_start_runs_thunderbird_on_the_profile_headless_when_there_is_no_display(tb, world):
    tb.start()
    run = wait_for(lambda: fake_runs(world / "profile"), "the fake to start")[0]
    assert argv_of(run) == ["--profile", str(world / "profile"), "--no-remote", "--headless"]
    assert tb.running()
    assert "DISPLAY" not in run["env"] and "WAYLAND_DISPLAY" not in run["env"] and "MOZ_ENABLE_WAYLAND" not in run["env"]


def test_a_start_is_a_process_of_its_own_in_a_group_of_its_own(tb, world):
    tb.start()
    run = wait_for(lambda: fake_runs(world / "profile"), "the fake to start")[0]
    assert run["ppid"] == os.getpid()
    assert run["pgid"] == run["pid"] == run["sid"] != os.getpgrp()


def test_the_environment_is_a_copy_without_the_persons_mozilla_switches(tb, world, monkeypatch):
    monkeypatch.setenv("MOZ_LOG", "IMAP:5")
    monkeypatch.setenv("MOZ_LOG_FILE", "/tmp/never")
    monkeypatch.setenv("DESKTOP_STARTUP_ID", "x")
    monkeypatch.setenv("BOMBADIL_MAIL_SOCKET", str(world / "run" / "mail.sock"))
    tb.start()
    run = wait_for(lambda: fake_runs(world / "profile"), "the fake to start")[0]
    keys = set(run["env_keys"])
    assert not {"MOZ_LOG", "MOZ_LOG_FILE", "DESKTOP_STARTUP_ID"} & keys
    assert {"BOMBADIL_MAIL_SOCKET", "HOME", "PATH"} <= keys, "the host Thunderbird starts finds mail.sock by these"
    assert run["env"]["MOZ_CRASHREPORTER_DISABLE"] == "1" and run["env"]["NO_AT_BRIDGE"] == "1"
    assert os.environ["MOZ_LOG"] == "IMAP:5", "the service's own environment is not touched"


def test_with_wayland_it_is_windowed_and_asked_for_wayland(tb, world, monkeypatch):
    (world / "xdg-run" / "wayland-test").write_text("")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-test")
    tb.start()
    run = wait_for(lambda: fake_runs(world / "profile"), "the fake to start")[0]
    assert "--headless" not in argv_of(run) and run["env"]["MOZ_ENABLE_WAYLAND"] == "1"
    assert run["env"]["WAYLAND_DISPLAY"] == "wayland-test"


def test_with_only_an_x_display_it_is_windowed_and_a_dead_wayland_socket_is_not_passed_on(tb, world, monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-gone")
    monkeypatch.setenv("DISPLAY", "somewhere.example:3")
    tb.start()
    run = wait_for(lambda: fake_runs(world / "profile"), "the fake to start")[0]
    assert "--headless" not in argv_of(run) and "MOZ_ENABLE_WAYLAND" not in run["env"]
    assert "WAYLAND_DISPLAY" not in run["env"] and run["env"]["DISPLAY"] == "somewhere.example:3"


def test_a_display_that_is_not_there_is_no_display_and_thunderbird_starts_headless(tb, world, monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-gone")
    monkeypatch.setenv("DISPLAY", ":987")
    tb.start()
    run = wait_for(lambda: fake_runs(world / "profile"), "the fake to start")[0]
    assert "--headless" in argv_of(run)
    assert "DISPLAY" not in run["env"] and "WAYLAND_DISPLAY" not in run["env"]


def test_display_kind_by_the_sockets_that_exist(world, tmp_path):
    (world / "xdg-run" / "wayland-1").write_text("")
    env = {"XDG_RUNTIME_DIR": str(world / "xdg-run")}
    assert engine.display_kind({**env, "WAYLAND_DISPLAY": "wayland-1"}) == "wayland"
    assert engine.display_kind({**env, "WAYLAND_DISPLAY": str(world / "xdg-run" / "wayland-1")}) == "wayland"
    assert engine.display_kind({**env, "WAYLAND_DISPLAY": "wayland-2"}) is None
    assert engine.display_kind({**env, "WAYLAND_DISPLAY": "wayland-2", "DISPLAY": "host:10.0"}) == "x11"
    assert engine.display_kind({**env, "DISPLAY": ":987"}) is None
    assert engine.display_kind({}) is None


def test_start_stop_and_running_are_idempotent(tb, world):
    profile = world / "profile"
    assert tb.running() is False
    tb.stop()                                    # nothing to stop
    tb.start()
    tb.start()                                   # nothing to start
    assert tb.running() and len(wait_for(lambda: fake_runs(profile), "the fake to start")) == 1
    pid = fake_runs(profile)[0]["pid"]
    wait_for(lambda: (profile / "lock").is_symlink(), "the lock")
    tb.stop()
    tb.stop()
    assert tb.running() is False and gone(pid)
    assert not (profile / "lock").exists(), "a clean stop is a clean exit"
    tb.start()
    assert tb.running() and len(wait_for(lambda: len(fake_runs(profile)) == 2 and fake_runs(profile), "again")) == 2


def test_stop_ends_the_whole_group_including_what_the_main_process_left(make, world, monkeypatch):
    monkeypatch.setenv("BOMBADIL_FAKE_TB", "child")
    tb = make()
    tb.start()
    run = wait_for(lambda: fake_runs(world / "profile"), "the fake to start")[0]
    assert not gone(run["child"])
    tb.stop()
    assert gone(run["pid"]) and wait_for(lambda: gone(run["child"]), "the child to be killed")


def test_a_thunderbird_that_will_not_quit_is_killed_after_the_time_it_was_given(make, world, monkeypatch):
    monkeypatch.setenv("BOMBADIL_FAKE_TB", "deaf,child")
    tb = make(stop_timeout=0.6)
    tb.start()
    run = wait_for(lambda: fake_runs(world / "profile"), "the fake to start")[0]
    started = time.monotonic()
    tb.stop()
    took = time.monotonic() - started
    assert 0.5 < took < 4, took
    assert gone(run["pid"]) and wait_for(lambda: gone(run["child"]), "the child to be killed")
    assert tb.running() is False


def test_a_slow_quit_is_waited_for_not_killed(make, world, monkeypatch):
    monkeypatch.setenv("BOMBADIL_FAKE_TB", "slow")
    tb = make(stop_timeout=5.0)
    tb.start()
    wait_for(lambda: fake_runs(world / "profile"), "the fake to start")
    tb.stop()
    assert not (world / "profile" / "lock").exists(), "it got to finish quitting, which is when it takes its lock away"


def test_a_crash_is_seen_by_running_and_leaves_a_lock_that_does_not_stop_the_next_start(make, world, monkeypatch):
    profile = world / "profile"
    monkeypatch.setenv("BOMBADIL_FAKE_TB", "crash")
    tb = make()
    tb.start()
    wait_for(lambda: fake_runs(profile), "the fake to start")
    wait_for(lambda: not tb.running(), "the crash to be seen")
    assert (profile / "lock").is_symlink(), "a crash leaves its lock as Thunderbird's does"
    stale = os.readlink(profile / "lock")
    monkeypatch.setenv("BOMBADIL_FAKE_TB", "")
    tb.start()
    runs = wait_for(lambda: len(fake_runs(profile)) == 2 and fake_runs(profile), "a second start")
    assert os.readlink(profile / "lock") != stale and tb.running()
    assert runs[1]["ppid"] == os.getpid()


def test_a_thunderbird_that_quits_by_itself_is_seen_and_its_leftovers_are_swept(make, world, monkeypatch):
    monkeypatch.setenv("BOMBADIL_FAKE_TB", "quick,child")
    tb = make()
    tb.start()
    run = wait_for(lambda: fake_runs(world / "profile"), "the fake to start")[0]
    wait_for(lambda: not tb.running(), "the exit to be seen")
    assert wait_for(lambda: gone(run["child"]), "what it left to be killed")


def test_a_lock_that_names_a_dead_process_or_some_other_program_is_no_thunderbird(tb, world):
    profile = world / "profile"
    profile.mkdir()
    sleeper = subprocess.Popen(["sleep", "30"])
    namesake = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "--profile", str(profile)])
    try:
        os.symlink(f"127.0.0.1:+{sleeper.pid}", profile / "lock")        # a live pid, but not a Thunderbird
        assert tb.running() is False
        (profile / "lock").unlink()
        os.symlink(f"127.0.0.1:+{namesake.pid}", profile / "lock")       # the profile on its command line, another program
        assert tb.running() is False
        (profile / "lock").unlink()
        os.symlink("127.0.0.1:+999999", profile / "lock")               # no such process
        assert tb.running() is False
        (profile / "lock").unlink()
        os.symlink("garbage", profile / "lock")
        assert tb.running() is False
        tb.start()
        assert tb.running()
    finally:
        for other in (sleeper, namesake):
            other.kill()
            other.wait()


def start_outside(fake_tb, profile: Path, *flags, own_group: bool = True) -> subprocess.Popen:
    """A Thunderbird an earlier run of the service left: started by something else, holding the profile's lock."""
    profile.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([str(fake_tb), "--profile", str(profile), "--no-remote", *flags],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=own_group)
    wait_for(lambda: (profile / "lock").is_symlink(), "its lock")
    return proc


def test_a_thunderbird_left_by_an_earlier_run_counts_as_running_and_is_not_started_twice(make, fake_tb, world):
    profile = world / "profile"
    orphan = start_outside(fake_tb, profile, "--headless")
    try:
        tb = make()
        assert tb.running() is True
        tb.start()
        assert len(fake_runs(profile)) == 1, "a second one on the same profile would only be refused"
        assert orphan.poll() is None
    finally:
        orphan.kill()
        orphan.wait()


def test_stop_ends_a_thunderbird_left_by_an_earlier_run_and_restart_replaces_it(make, fake_tb, world):
    profile = world / "profile"
    orphan = start_outside(fake_tb, profile)
    tb = make()
    tb.stop()
    assert orphan.wait(5) == 0 and tb.running() is False
    orphan = start_outside(fake_tb, profile)
    try:
        tb.restart()
        assert orphan.wait(5) == 0
        runs = wait_for(lambda: len(fake_runs(profile)) == 3 and fake_runs(profile), "the replacement")
        assert runs[-1]["ppid"] == os.getpid() and tb.running()
    finally:
        orphan.kill()
        orphan.wait()


def test_stop_kills_the_whole_group_of_an_earlier_run_that_will_not_quit(make, fake_tb, world, monkeypatch):
    monkeypatch.setenv("BOMBADIL_FAKE_TB", "deaf,child")
    profile = world / "profile"
    orphan = start_outside(fake_tb, profile)
    try:
        child = wait_for(lambda: fake_runs(profile), "its record")[0]["child"]
        make(stop_timeout=0.5).stop()
        assert orphan.wait(5) == -signal.SIGKILL
        assert wait_for(lambda: gone(child), "what it left in its group to be killed")
    finally:
        orphan.kill()
        orphan.wait()


def test_stop_kills_only_the_process_of_an_earlier_run_that_shares_our_group(make, fake_tb, world, monkeypatch):
    """Not a group leader: its group is somebody else's (the shell that started it), and is left alone."""
    monkeypatch.setenv("BOMBADIL_FAKE_TB", "deaf")
    orphan = start_outside(fake_tb, world / "profile", own_group=False)
    try:
        make(stop_timeout=0.5).stop()
        assert orphan.wait(5) == -signal.SIGKILL
    finally:
        orphan.kill()
        orphan.wait()


def test_stopping_what_has_just_gone_or_is_not_ours_to_signal_is_nothing(make, monkeypatch):
    tb = make()
    tb._terminate(2_000_000_000, None)               # no such process
    monkeypatch.setattr(engine.os, "kill", lambda *_: (_ for _ in ()).throw(PermissionError()))
    tb._terminate(1, None)                           # somebody else's


def test_a_thunderbird_that_cannot_be_executed_is_a_sentence_not_a_traceback(make, world):
    broken = world / "bin" / "thunderbird-broken"
    broken.write_text("#!/nonexistent/interpreter\n")
    broken.chmod(0o755)
    tb = make(binary=str(broken))
    with pytest.raises(RuntimeError, match="could not be started"):
        tb.start()
    assert tb.running() is False


def test_a_thunderbird_on_another_profile_is_not_taken_for_ours(make, fake_tb, world):
    other = start_outside(fake_tb, world / "other-profile")
    try:
        profile = world / "profile"
        profile.mkdir()
        os.symlink(f"127.0.0.1:+{other.pid}", profile / "lock")
        assert make().running() is False
    finally:
        other.kill()
        other.wait()


@pytest.mark.parametrize("state", ["stopped", "running", "crashed", "orphan"])
def test_restart_works_from_any_state(make, fake_tb, world, monkeypatch, state):
    profile = world / "profile"
    orphan = None
    if state == "crashed":
        monkeypatch.setenv("BOMBADIL_FAKE_TB", "crash")
    tb = make()
    if state in ("running", "crashed"):
        tb.start()
        wait_for(lambda: fake_runs(profile), "the fake to start")
    if state == "crashed":
        wait_for(lambda: not tb.running(), "the crash")
        monkeypatch.setenv("BOMBADIL_FAKE_TB", "")
    if state == "orphan":
        orphan = start_outside(fake_tb, profile)
    before = len(fake_runs(profile))
    try:
        tb.restart()
        runs = wait_for(lambda: len(fake_runs(profile)) == before + 1 and fake_runs(profile), "the new start")
        assert tb.running() and runs[-1]["ppid"] == os.getpid()
        pids = [r["pid"] for r in runs[:-1]]
        assert all(wait_for(lambda p=p: gone(p), "the old one to be gone") for p in pids)
    finally:
        if orphan is not None:
            orphan.kill()
            orphan.wait()


def test_restart_from_a_start_that_cannot_happen_says_why_and_leaves_nothing_running(make, world):
    tb = make(binary=str(world / "none"))
    with pytest.raises(RuntimeError, match="not installed"):
        tb.restart()
    assert tb.running() is False


def test_thunderbirds_output_goes_to_a_capped_log_that_nobody_can_fill_the_disk_with(make, world, monkeypatch):
    monkeypatch.setenv("BOMBADIL_FAKE_TB", "noisy:300000")
    tb = make(log_cap=64 << 10)
    tb.start()
    log = world / "state" / "mail-engine.log"
    wait_for(lambda: log.exists() and (world / "state" / "mail-engine.log.1").exists(), "the log to rotate")
    tb.stop()
    sizes = [p.stat().st_size for p in world.glob("state/mail-engine.log*")]
    assert sizes and max(sizes) <= 64 << 10 and sum(sizes) <= 128 << 10
    assert stat.S_IMODE(log.stat().st_mode) == 0o600
    assert tb.running() is False, "a Thunderbird whose output is read all the way never blocks on it"


def test_the_log_is_appended_to_across_starts_and_the_reader_ends_with_the_process(make, world, monkeypatch):
    monkeypatch.setenv("BOMBADIL_FAKE_TB", "noisy:1000")
    tb = make()
    log = world / "state" / "mail-engine.log"
    tb.start()
    wait_for(lambda: log.exists() and log.stat().st_size >= 1000, "the first output")
    tb.stop()
    tb.start()
    wait_for(lambda: log.stat().st_size >= 2000, "the second output")
    tb.stop()
    assert not [t for t in threading.enumerate() if t.name == "mail-engine-log"], "no reader outlives its process"


def test_start_without_thunderbird_raises_a_sentence(make, world):
    tb = make(binary=str(world / "none"))
    with pytest.raises(RuntimeError, match="Thunderbird is not installed"):
        tb.start()
    assert not (world / "profile" / "lock").exists()


# -- the window --

CLIENTS = [
    {"address": "0xa1", "mapped": True, "hidden": False, "class": "thunderbird", "initialClass": "thunderbird",
     "title": "Inbox - maya@acme.example - Mozilla Thunderbird", "pid": 4242, "focusHistoryID": 2,
     "workspace": {"id": -99, "name": "special:mail-engine"}},
    {"address": "0xa2", "mapped": True, "hidden": False, "class": "thunderbird", "initialClass": "thunderbird",
     "title": "Login to account \"maya@acme.example\" failed", "pid": 4242, "focusHistoryID": 1,
     "workspace": {"id": -99, "name": "special:mail-engine"}},
    {"address": "0xa3", "mapped": True, "hidden": False, "class": "thunderbird", "initialClass": "thunderbird",
     "title": "Write: Re: the plan - Thunderbird", "pid": 4242, "focusHistoryID": 0,
     "workspace": {"id": -99, "name": "special:mail-engine"}},
    {"address": "0xb1", "mapped": True, "hidden": False, "class": "bombadil-app-mail", "title": "Mail", "pid": 77,
     "focusHistoryID": 0, "workspace": {"id": 1, "name": "1"}},
    {"address": "0xc1", "mapped": True, "hidden": False, "class": "foot", "title": "thunderbird", "pid": 78,
     "focusHistoryID": 3, "workspace": {"id": 1, "name": "1"}},
]


@pytest.mark.parametrize("cls, yes", [
    ("thunderbird", True), ("Thunderbird", True), ("THUNDERBIRD", True), ("org.mozilla.thunderbird", True),
    ("net.thunderbird.Thunderbird", True), ("thunderbird-esr", True), ("bombadil-app-mail", False),
    ("firefox", False), ("", False), ("mythunderbird", False), ("bombadil-app-thunderbird", False), (None, False),
])
def test_which_classes_are_thunderbirds_windows(cls, yes):
    assert engine.is_engine_window({"class": cls}) is yes
    assert engine.is_engine_window({"class": "x", "initialClass": cls}) is yes


def test_the_pattern_is_the_one_the_window_rule_of_the_image_uses():
    lua = (REPO / "iso" / "airootfs" / "etc" / "skel" / ".config" / "hypr" / "hyprland.lua").read_text()
    rule = next(ln for ln in lua.splitlines() if 'name = "mail-engine"' in ln)
    assert 'class = "(?i)^(.*\\\\.)?thunderbird.*$"' in rule and engine._WINDOW_CLASS.pattern == "(?i)^(.*\\.)?thunderbird.*$"
    assert 'workspace = "special:mail-engine silent"' in rule and engine.ENGINE_WORKSPACE == "special:mail-engine"


def test_only_mapped_visible_windows_of_thunderbird_are_candidates():
    clients = [*CLIENTS, {"address": "0xd1", "mapped": False, "class": "thunderbird"},
               {"address": "0xd2", "mapped": True, "hidden": True, "class": "thunderbird"},
               {"address": "0xd3", "mapped": True, "class": "thunderbird", "title": "Thunderbird", "size": [10, 10]}]
    assert [c["address"] for c in engine.engine_windows(clients)] == ["0xa1", "0xa2", "0xa3"]
    assert engine.engine_windows([]) == [] and engine.engine_windows([{"class": "foot"}]) == []


def test_a_dialog_is_brought_before_the_main_window_and_the_last_focused_before_the_rest():
    assert engine.pick_window(CLIENTS)["address"] == "0xa2", "the login dialog, not the main window"
    assert engine.pick_window(CLIENTS[:1] + CLIENTS[2:3])["address"] == "0xa1", "never a compose window"
    assert engine.pick_window(CLIENTS[2:3]) is None, "not even when it is all there is"
    speck = {"address": "0xd3", "mapped": True, "class": "thunderbird", "title": "Thunderbird", "size": [10, 10],
             "focusHistoryID": 0}
    assert engine.pick_window([speck, *CLIENTS[:1]])["address"] == "0xa1", "the 10 by 10 window GTK keeps is no dialog"
    assert engine.pick_window([{**speck, "size": [530, 114]}, *CLIENTS[:1]])["address"] == "0xd3", "a small dialog is"
    assert engine.pick_window([{**speck, "size": "big"}])["address"] == "0xd3", "an odd size is no reason to refuse"
    main_only = [c for c in CLIENTS if c["address"] == "0xa1"]
    assert engine.pick_window(main_only)["address"] == "0xa1"
    two = [{**CLIENTS[1], "address": "0xe1", "focusHistoryID": 5}, {**CLIENTS[1], "address": "0xe2", "focusHistoryID": 3},
           {**CLIENTS[1], "address": "0xe3", "focusHistoryID": -1}]
    assert engine.pick_window(two)["address"] == "0xe2"
    assert engine.pick_window([]) is None and engine.pick_window(CLIENTS[3:]) is None
    assert engine.pick_window([{"class": "thunderbird", "title": None}])["class"] == "thunderbird"


def test_the_selector_is_the_pid_as_the_launcher_does_else_the_class():
    assert engine.window_selector({"pid": 4242, "class": "thunderbird"}) == "pid:4242"
    assert engine.window_selector({"class": "net.thunderbird.Thunderbird"}) == "class:^(net\\.thunderbird\\.Thunderbird)$"
    assert engine.window_selector({"pid": -1, "initialClass": "thunderbird"}) == "class:^(thunderbird)$"


def test_which_monitor_shows_the_engines_workspace():
    monitors = [{"name": "DP-1", "focused": True, "specialWorkspace": {"name": ""}},
                {"name": "HDMI-A-1", "focused": False, "specialWorkspace": {"name": "special:mail-engine"}}]
    assert engine.staged_monitor(monitors)["name"] == "HDMI-A-1"
    assert engine.staged_monitor([{"name": "DP-1", "specialWorkspace": {"name": "special:browser"}}]) is None
    assert engine.staged_monitor([{"name": "DP-1"}, None, "x", {"specialWorkspace": "special:mail-engine"}]) is None


class FakeHypr:
    """Hyprland's two doors as the engine uses them: `request("j/...")` answers with JSON, `dispatch(lua)` is kept."""

    def __init__(self, clients=CLIENTS, monitors=None, available=True):
        self.clients_now = clients
        self.monitors = monitors if monitors is not None else [
            {"name": "DP-1", "focused": True, "specialWorkspace": {"name": ""}}]
        self._available = available
        self.sent: list[str] = []
        self.asked: list[str] = []

    @property
    def available(self):
        return self._available

    def request(self, command, timeout=10):
        self.asked.append(command)
        if command == "j/clients":
            now = self.clients_now() if callable(self.clients_now) else self.clients_now
            return json.dumps(now)
        if command == "j/monitors":
            return json.dumps(self.monitors)
        raise AssertionError(command)

    def dispatch(self, lua):
        self.sent.append(lua)
        return "ok"


def test_staging_shows_the_engines_workspace_by_focusing_the_dialog(make, world, windowed):
    hypr = FakeHypr()
    tb = make(hyprland=hypr)
    tb.start()
    assert tb.stage(True) is True
    assert hypr.sent == ['hl.dsp.focus({ window = "pid:4242" })']


def test_staging_without_hyprland_is_false_not_an_error(make, world):
    tb = make(hyprland=FakeHypr(available=False))
    tb.start()
    assert tb.stage(True) is False and tb.stage(False) is False
    ThunderbirdProcess().stage(True)    # the real door, with no compositor in the test's environment


def test_staging_a_thunderbird_that_is_not_running_or_has_no_window_is_false(make, world, windowed, monkeypatch):
    monkeypatch.setattr(engine, "WINDOW_WAIT_S", 0.3)
    hypr = FakeHypr()
    tb = make(hyprland=hypr)
    assert tb.stage(True) is False and hypr.sent == [] and hypr.asked == []
    tb.start()
    hypr.clients_now = CLIENTS[3:]
    started = time.monotonic()
    assert tb.stage(True) is False
    assert time.monotonic() - started < 2 and hypr.sent == []


def test_a_window_that_has_not_mapped_yet_is_waited_for_a_little(make, world, windowed, monkeypatch):
    seen = []

    def clients():
        seen.append(1)
        return CLIENTS if len(seen) >= 3 else []

    hypr = FakeHypr(clients)
    tb = make(hyprland=hypr)
    tb.start()
    assert tb.stage(True) is True and len(seen) >= 3 and hypr.sent == ['hl.dsp.focus({ window = "pid:4242" })']


def test_hyprland_that_fails_is_false_and_never_an_exception(make, world, windowed, monkeypatch):
    monkeypatch.setattr(engine, "WINDOW_WAIT_S", 0.3)
    shown = [{"name": "DP-1", "focused": True, "specialWorkspace": {"name": "special:mail-engine"}}]

    class Broken(FakeHypr):
        def request(self, command, timeout=10):
            raise OSError("socket closed")

    class Garbled(FakeHypr):
        def request(self, command, timeout=10):
            return "not json"

    class Hangs(FakeHypr):          # hyprctl that does not answer: subprocess's own timeout
        def dispatch(self, lua):
            raise subprocess.TimeoutExpired(["hyprctl", "dispatch", lua], 15)

    class Odd(FakeHypr):            # JSON, but not the shapes Hyprland answers with
        def request(self, command, timeout=10):
            return json.dumps([{"specialWorkspace": 5}] if command == "j/monitors" else {"not": "a list"})

    # (what staging in answers, what putting away answers) for each way the compositor can be wrong
    for hypr, wanted in ((Broken(), (False, False)), (Garbled(), (False, False)), (Hangs(monitors=shown), (False, False)),
                         (Odd(), (False, True))):
        tb = make(hyprland=hypr)
        tb.start()
        assert (tb.stage(True), tb.stage(False)) == wanted, type(hypr).__name__


def test_putting_it_away_toggles_the_workspace_only_when_it_is_shown(make, world):
    hypr = FakeHypr()
    tb = make(hyprland=hypr)
    assert tb.stage(False) is True and hypr.sent == [], "already away"
    hypr.monitors = [{"name": "DP-1", "focused": True, "specialWorkspace": {"name": "special:mail-engine"}}]
    assert tb.stage(False) is True
    assert hypr.sent == ['hl.dsp.workspace.toggle_special("mail-engine")']


def test_putting_it_away_first_focuses_the_monitor_it_is_on(make, world):
    hypr = FakeHypr(monitors=[{"name": "DP-1", "focused": True, "specialWorkspace": {"name": ""}},
                              {"name": "HDMI-A-1", "focused": False, "specialWorkspace": {"name": "special:mail-engine"}}])
    assert make(hyprland=hypr).stage(False) is True
    assert hypr.sent == ['hl.dsp.focus({ monitor = "HDMI-A-1" })', 'hl.dsp.workspace.toggle_special("mail-engine")']


def test_a_headless_thunderbird_is_started_again_with_a_window_when_there_is_a_display_now(make, world, monkeypatch):
    profile = world / "profile"

    def clients():
        return CLIENTS if len(fake_runs(profile)) >= 2 else []

    hypr = FakeHypr(clients)
    tb = make(hyprland=hypr)
    tb.start()
    assert "--headless" in fake_runs_wait(profile, 1)[0]["argv"]
    assert tb.stage(True) is False and len(fake_runs(profile)) == 1, "with no display there is no window to be had"
    monkeypatch.setenv("DISPLAY", "somewhere.example:3")
    assert tb.stage(True) is True
    runs = fake_runs_wait(profile, 2)
    assert "--headless" not in runs[1]["argv"] and runs[1]["env"]["DISPLAY"] == "somewhere.example:3"
    assert gone(runs[0]["pid"]) and tb.running()
    assert hypr.sent == ['hl.dsp.focus({ window = "pid:4242" })']
    tb.restart()                       # a restart keeps the mode: the display is still there
    assert "--headless" not in fake_runs_wait(profile, 3)[2]["argv"]


def fake_runs_wait(profile, count):
    return wait_for(lambda: len(fake_runs(profile)) >= count and fake_runs(profile), f"{count} starts")


def test_a_windowed_thunderbird_is_not_restarted_to_be_shown(make, world, windowed):
    profile = world / "profile"
    hypr = FakeHypr()
    tb = make(hyprland=hypr)
    tb.start()
    fake_runs_wait(profile, 1)
    assert tb.stage(True) is True and len(fake_runs(profile)) == 1


# -- a real Thunderbird --


def real_thunderbird() -> str | None:
    named = os.environ.get("BOMBADIL_TEST_THUNDERBIRD")
    default = Path.home() / ".cache" / "bombadil-lab" / "thunderbird-157.0" / "thunderbird"
    for candidate in ([named] if named else [str(default)]):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


REAL_THUNDERBIRD = real_thunderbird()    # found now: a test's HOME is not the one the lab's copy is under


def processes_on(profile: Path) -> list[int]:
    """Every process whose command line names the profile (Thunderbird and what it started with it)."""
    found = []
    for entry in os.scandir("/proc"):
        if entry.name.isdigit():
            try:
                if str(profile).encode() in Path(entry.path, "cmdline").read_bytes():
                    found.append(int(entry.name))
            except OSError:
                pass
    return found


EXTENSION_JS = """\
// {marker}
const port = messenger.runtime.connectNative("bombadil_mail");
port.postMessage({{event: "hello", marker: "{marker}", app: "stand-in"}});
"""


@pytest.mark.skipif(REAL_THUNDERBIRD is None, reason="no Thunderbird unpacked (BOMBADIL_TEST_THUNDERBIRD)")
@pytest.mark.skipif(shutil.which("dovecot") is None, reason="no Dovecot for the lab's local mail server")
def test_a_real_thunderbird_starts_unseen_with_the_seeded_account_and_leaves_nothing(world, monkeypatch, request):
    """prepare, seed (the lab's local mail server, a password the lab's login store has), start: Thunderbird stays
    up with no window at all, loads the add-on from the .xpi, starts the real host through the native-messaging
    manifest and syncs the account; a kill leaves a lock that a restart does not mind and a changed add-on is the
    one that runs; stop leaves no process; forgetting removes the account from the files it really wrote."""
    sys.path.insert(0, str(LAB))
    import mailserver
    import seed_logins

    binary = REAL_THUNDERBIRD
    base = Path(tempfile.mkdtemp(prefix="bombadil-engine-test-"))
    os.chmod(base, 0o755)      # the lab's server runs as nobody when the tests run as root
    imap_port, smtp_port = free_ports(2)
    server = mailserver.MailServer(str(base / "mail"), imap_port, smtp_port)
    service = None
    tb = None
    groups: list[int] = []

    def cleanup():
        if tb is not None:
            tb.stop()
        for pgid in groups:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass
        if service is not None:
            service.close()
        server.stop()
        shutil.rmtree(base, ignore_errors=True)

    request.addfinalizer(cleanup)
    server.start(seed=str(LAB / "seed"))

    sock_path = world / "run" / "mail.sock"
    sock_path.parent.mkdir()
    monkeypatch.setenv("BOMBADIL_MAIL_SOCKET", str(sock_path))
    service = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    service.bind(str(sock_path))
    service.listen(4)

    ext = write_addon(world / "extension", optional=())
    (ext / "background.js").write_text(EXTENSION_JS.format(marker="one"))
    tb = ThunderbirdProcess(binary=binary, addon_dir=ext, host=REPO / "bin" / "bombadil-mail-host")
    lab_server = accts.Provider("imap", "Lab", "127.0.0.1", imap_port, "none", "127.0.0.1", smtp_port, "none", "password",
                                "", "", 20_000_000, "")
    user = mailserver.USER
    tb.prepare()
    tb.seed_account({"id": "a1", "email": user, "name": "", "sender": "Lab Tester"}, lab_server)
    seed_logins.seed(str(tb.profile), [("imap://127.0.0.1", "imap://127.0.0.1", user, mailserver.PASSWORD),
                                       ("smtp://127.0.0.1", "smtp://127.0.0.1", user, mailserver.PASSWORD)],
                     str(Path(binary).parent))

    def hello_from_addon():
        """Accept the host's connection and read lines until the add-on's hello: (host's, add-on's)."""
        service.settimeout(60)
        conn, _ = service.accept()
        conn.settimeout(60)
        buf = b""
        lines = []
        while len(lines) < 2:
            data = conn.recv(65536)
            assert data, "the host hung up"
            buf += data
            *done, buf = buf.split(b"\n")
            lines += [json.loads(x) for x in done]
        return lines, conn

    tb.start()
    pid = wait_for(lambda: fake_pid(tb.profile), "Thunderbird's lock", 30)
    groups.append(pid)
    (host_hello, addon_hello), conn = hello_from_addon()
    assert host_hello["op"] == "engine_hello" and addon_hello == {"event": "hello", "marker": "one", "app": "stand-in"}

    cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    assert b"--headless" in cmdline and b"--no-remote" in cmdline, "no display: no window"
    environ = dict(x.partition(b"=")[::2] for x in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0") if x)
    assert b"DISPLAY" not in environ and b"WAYLAND_DISPLAY" not in environ
    mail_store = tb.profile / "ImapMail" / "127.0.0.1-2"
    wait_for(lambda: (mail_store / "INBOX").exists() and (mail_store / "INBOX").stat().st_size > 0,
             "the account to sync into its own folder", 60)
    time.sleep(3)
    assert tb.running(), "it stays up"
    assert not (tb.profile / "lock").is_symlink() or os.readlink(tb.profile / "lock").endswith(f"+{pid}")

    # a kill leaves a stale lock; a changed add-on is the one that runs after the restart
    os.killpg(pid, signal.SIGKILL)
    wait_for(lambda: not tb.running(), "the kill to be seen")
    assert (tb.profile / "lock").is_symlink(), "the crash's lock is still there"
    conn.close()
    (ext / "background.js").write_text(EXTENSION_JS.format(marker="two"))
    tb.prepare()
    tb.seed_account({"id": "a1", "email": user, "name": "", "sender": "Lab Tester"}, lab_server)
    tb.start()
    pid2 = wait_for(lambda: fake_pid(tb.profile, not_pid=pid), "the new Thunderbird's lock", 30)
    groups.append(pid2)
    (_, addon_hello), conn = hello_from_addon()
    assert addon_hello["marker"] == "two", "the add-on that was changed is the add-on that runs"
    conn.close()

    started = time.monotonic()
    tb.stop()
    assert time.monotonic() - started < 12
    assert tb.running() is False and wait_for(lambda: not processes_on(tb.profile), "no process to be left", 15) is True
    assert not (tb.profile / "lock").is_symlink(), "a clean quit takes its lock away"
    assert gone(pid2)

    tb.forget_account({"id": "a1", "email": user})
    prefs = (tb.profile / "prefs.js").read_text()
    assert user not in prefs and "127.0.0.1-2" not in prefs and "server2." not in prefs
    assert user not in user_js(tb.profile) and not mail_store.exists()
    assert login_hosts(tb.profile) == []

    # and the add-on that ships, on the same profile: it is the .xpi that Thunderbird loads, its optional
    # permission (sending) is granted by the engine alone, and it says so in its hello
    shipped = ThunderbirdProcess(binary=binary, host=REPO / "bin" / "bombadil-mail-host")
    if not (shipped.addon_dir() / "manifest.json").is_file():
        return
    tb = shipped
    tb.prepare()
    tb.start()
    groups.append(wait_for(lambda: fake_pid(tb.profile, not_pid=pid2), "the shipped add-on's Thunderbird", 30))
    (_, addon_hello), conn = hello_from_addon()
    assert addon_hello["event"] == "hello" and "messages_send" in addon_hello["caps"], addon_hello
    conn.close()
    tb.stop()
    assert tb.running() is False and wait_for(lambda: not processes_on(tb.profile), "no process to be left", 15) is True


def free_ports(n: int) -> list[int]:
    socks = [socket.socket() for _ in range(n)]
    try:
        for s in socks:
            s.bind(("127.0.0.1", 0))
        return [s.getsockname()[1] for s in socks]
    finally:
        for s in socks:
            s.close()


def fake_pid(profile: Path, not_pid: int | None = None) -> int | None:
    """The pid a profile's lock names, when it is that of a live process."""
    try:
        pid = int(os.readlink(profile / "lock").rpartition("+")[2])
    except (OSError, ValueError):
        return None
    return pid if pid != not_pid and not gone(pid) else None

"""The whole chain on a real Thunderbird: the service, the engine that runs it, the add-on, the host and a mail server.

Nothing here is faked except the mail server, which is the lab's local Dovecot and SMTP sink on 127.0.0.1
(tests/mail/lab/FINDINGS.md): the `Service` runs with a real `EngineLink` and a real `ThunderbirdProcess`, which
starts Thunderbird headless on a profile it wrote, with the shipped add-on loaded and the real
`bin/bombadil-mail-host` started through the native-messaging manifest. A client talks to mail.sock the way the
window does. What it follows is one working morning: the account is added, its mail is listed and read, mail that
arrives is a push, a reply is drafted, shown and pressed, and the SMTP sink holds exactly that message, threaded
under the one it answers; then the service stops and nothing of Thunderbird's session is left.

Skips without an unpacked Thunderbird (BOMBADIL_TEST_THUNDERBIRD, else the lab's copy) and without Dovecot. The
lab's server runs as nobody when the tests run as root, so the temp dir is made world-readable. Every HTTP and
HTTPS request Thunderbird makes goes to nothing: the profile's proxy is a closed port on this computer.
"""

import asyncio
import os
import shutil
import signal
import sys
import tempfile
import time
from pathlib import Path

import pytest
from test_mail_engine import LAB, REAL_THUNDERBIRD, REPO, free_ports, session_members
from test_mail_service import Client, until

from bombadil import paths, procs
from bombadil.mail import accounts as accts
from bombadil.mail import bridge, service
from bombadil.mail.engine import ThunderbirdProcess
from bombadil.mail.service import Service

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(REAL_THUNDERBIRD is None, reason="no Thunderbird unpacked (BOMBADIL_TEST_THUNDERBIRD)"),
    pytest.mark.skipif(shutil.which("dovecot") is None, reason="no Dovecot for the lab's local mail server"),
]

NEW_MAIL = (b"From: Dana Example <dana@example.org>\r\nTo: Lab Tester <test@example.test>\r\n"
            b"Subject: Printer on the second floor\r\nMessage-ID: <printer-1@example.org>\r\n"
            b"Date: Thu, 01 Oct 2026 09:00:00 +0000\r\n\r\nThe printer is out of paper again.\r\n")


async def wait(fn, timeout=60.0):
    return await until(fn, timeout, 0.25)


async def test_a_morning_of_mail_on_a_real_thunderbird_from_the_account_to_the_press_and_back_to_nothing(
        tmp_path, monkeypatch, request):
    sys.path.insert(0, str(LAB))
    import mailserver
    import seed_logins

    base = Path(tempfile.mkdtemp(prefix="bombadil-e2e-"))
    os.chmod(base, 0o755)
    imap_port, smtp_port = free_ports(2)
    server = mailserver.MailServer(str(base / "mail"), imap_port, smtp_port)
    home = base / "home"
    state = base / "state"
    for d in (home, state, base / "run"):
        d.mkdir()
    for var, where in (("HOME", home), ("XDG_RUNTIME_DIR", base / "run"), ("BOMBADIL_STATE", state),
                       ("BOMBADIL_RUNTIME", base / "run"), ("BOMBADIL_MAIL_DB", state / "mail.db"),
                       ("BOMBADIL_MAIL_FILES", state / "mail"), ("BOMBADIL_MAIL_PROFILE", base / "profile"),
                       ("BOMBADIL_PRESS_LOG", state / "presses.jsonl"),
                       ("BOMBADIL_MAIL_SOCKET", base / "run" / "mail.sock")):
        monkeypatch.setenv(var, str(where))
    for var in ("DISPLAY", "WAYLAND_DISPLAY"):
        monkeypatch.delenv(var, raising=False)       # no screen: headless, and nothing to put on one
    monkeypatch.setattr(procs, "scope_supported", lambda: True)
    # What this machine would resolve is the lab's server, whatever the address says.
    lab = accts.Provider("imap", "Lab", "127.0.0.1", imap_port, "none", "127.0.0.1", smtp_port, "none", "password",
                         "", "", 20_000_000, "")
    monkeypatch.setattr(accts, "detect", lambda email, resolver=None: lab)
    monkeypatch.setitem(accts.PROVIDERS, "imap", lab)
    monkeypatch.setattr(service, "STABLE_S", 5.0)

    tb = ThunderbirdProcess(binary=REAL_THUNDERBIRD, host=REPO / "bin" / "bombadil-mail-host")
    svc = task = client = None
    pgids: list[int] = []

    def cleanup():
        for pgid in pgids:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass
        server.stop()
        shutil.rmtree(base, ignore_errors=True)

    request.addfinalizer(cleanup)
    server.start(seed=str(LAB / "seed"))
    user = mailserver.USER
    tb.prepare()
    seed_logins.seed(str(tb.profile), [("imap://127.0.0.1", "imap://127.0.0.1", user, mailserver.PASSWORD),
                                       ("smtp://127.0.0.1", "smtp://127.0.0.1", user, mailserver.PASSWORD)],
                     str(Path(REAL_THUNDERBIRD).parent))
    # Mozilla's servers are not reachable from here, and nothing may try: a proxy that is a closed port.
    prefs = tb.profile / "user.js"
    prefs.write_text(prefs.read_text() + 'user_pref("network.proxy.type", 1);\n'
                     'user_pref("network.proxy.http", "127.0.0.1");\nuser_pref("network.proxy.http_port", 9);\n'
                     'user_pref("network.proxy.ssl", "127.0.0.1");\nuser_pref("network.proxy.ssl_port", 9);\n'
                     'user_pref("network.proxy.no_proxies_on", "localhost, 127.0.0.1");\n')

    svc = Service(engine=bridge.EngineLink(), process=tb, resolver=lambda d: [], in_turn=lambda pid: False)
    task = asyncio.ensure_future(svc.serve())
    try:
        await until(lambda: svc.socket_path.exists() or task.done())
        reader, writer = await asyncio.open_unix_connection(str(svc.socket_path), limit=1 << 26)
        client = Client(reader, writer)
        await client.call("subscribe")
        assert (await client.call("status"))["engine"] == "off"           # no account: nothing is started

        # 1. the account is added, Thunderbird starts by itself and its add-on says hello
        added = await client.call("add_account", email=user)
        assert added["email"] == user and added["provider"] == "imap"
        await wait(lambda: svc.engine_state == "up")
        pid = int(os.readlink(tb.profile / "lock").rpartition("+")[2])
        pgids.append(pid)
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        assert b"--headless" in cmdline
        assert (home / ".mozilla" / "native-messaging-hosts" / "bombadil_mail.json").is_file()

        async def account_ok():
            [a] = (await client.call("accounts", fresh=True))["accounts"]
            return a["state"] == "ok" and a
        a = await wait(account_ok)
        assert a["unread"] >= 1 and a["email"] == user

        # 2. its mail is listed, newest first, and a mail reads as text and stays unread
        listed = await wait(lambda: client.call("list", view="all", limit=50))
        subjects = [m["subject"] for m in listed["messages"]]
        assert "Welcome to the lab" in subjects and any("Gr" in s for s in subjects), subjects
        assert [m["ts"] for m in listed["messages"]] == sorted((m["ts"] for m in listed["messages"]), reverse=True)
        welcome = next(m for m in listed["messages"] if m["subject"] == "Welcome to the lab")
        read = await client.call("read", id=welcome["id"])
        assert "lab" in read["text"].lower() and read["message"]["unread"] is True
        assert (await client.call("search", text="Welcome"))["messages"][0]["id"] == welcome["id"]

        # 3. mail that arrives is a push, from IDLE, within seconds
        while not client.pushes.empty():
            client.pushes.get_nowait()
        server.deliver(NEW_MAIL)
        pushed = await client.push("new_mail", timeout=45)
        assert pushed["message"]["subject"] == "Printer on the second floor"
        assert pushed["message"]["from"]["email"] == "dana@example.org" and pushed["known"] is False

        # 4. a mark and a flag are kept by the server
        await client.call("mark_reply", id=pushed["message"]["id"], needs=True, why="Asks for paper.")
        assert [m["why"] for m in (await client.call("list", view="needs_reply"))["messages"]] == ["Asks for paper."]
        await client.call("set_flags", id=welcome["id"], flagged=True)

        # 5. a reply: drafted, shown, pressed; the sink holds that message, threaded under the one it answers
        thread = next(m for m in (await client.call("search", text="Project plan"))["messages"])
        draft = await client.call("draft", kind="reply", reply_to=thread["id"], body="Thursday works for me.")
        assert draft["warnings"] == [] and draft["state"] == "open"
        shown = await client.call("draft_shown", id=draft["id"], fingerprint=draft["fingerprint"])
        assert shown["shown"] is True
        sent = await client.call("send", _timeout=90, id=draft["id"], fingerprint=draft["fingerprint"])
        assert sent["already"] is False and sent["receipt"]["line"].startswith("Sent to ")
        [eml] = [server.sent_eml(i) for i in range(1, 2)] if hasattr(server, "sent_eml") else [b""]
        text = eml.decode(errors="replace") if isinstance(eml, bytes) else str(eml)
        assert "Thursday works for me." in text and "In-Reply-To:" in text and "Re: " in text
        assert (await client.call("draft_get", id=draft["id"]))["state"] == "sent"
        # the same press again sends nothing more
        again = await client.call("send", id=draft["id"], fingerprint=draft["fingerprint"])
        assert again["already"] is True and len(server.sent()) == 1

        # 6. the service stops, and with it Thunderbird: nothing of its session is left
        svc.stop()
        await asyncio.wait_for(task, 30)
        task = None
        await wait(lambda: not session_members(pid), 20)
        assert not (tb.profile / "lock").exists() or not os.path.exists(f"/proc/{pid}")
    finally:
        if client is not None:
            client.close()
        if task is not None:
            svc.stop()
            try:
                await asyncio.wait_for(task, 30)
            except (asyncio.CancelledError, TimeoutError):
                pass
        time.sleep(0.1)
    assert paths.mail_socket().exists() is False

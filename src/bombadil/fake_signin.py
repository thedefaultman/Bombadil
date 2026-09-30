#!/usr/bin/env python3
"""A stand-in for a provider CLI's login, for the tests and the VM smoke test: no account needed.

It behaves like the real ones. A server on localhost plays the provider's sign-in page; the
URL goes to $BROWSER and is printed with a paste prompt, as Claude Code does; the page comes
back to the CLI's localhost callback, or ends on a code for the prompt.

  fake_signin.py auto     the page returns to the localhost callback by itself
  fake_signin.py manual   $BROWSER is not used; the printed page ends on a code to paste
  fake_signin.py never    the page never finishes (timeouts, a closed panel)
  fake_signin.py fail     the page comes back with an error

Signed in means $HOME/.fake-signin exists. FAKE_SIGNIN_DELAY is how long the page shows
before it moves on (seconds, default 2). Standard library only: it runs from the ISO as is.
"""

import http.server
import os
import secrets
import shlex
import subprocess
import sys
import threading
import urllib.parse
from pathlib import Path

CREDENTIALS = Path.home() / ".fake-signin"


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "auto"
    delay = float(os.environ.get("FAKE_SIGNIN_DELAY") or 2)
    state, code = secrets.token_urlsafe(12), secrets.token_urlsafe(16)
    outcome: dict = {}
    finished = threading.Event()

    def finish(ok: bool, said: str):
        outcome.update(ok=ok, said=said)
        if ok:
            CREDENTIALS.write_text("signed in\n")
        finished.set()

    class Page(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body: str, status: int = 200, location: str | None = None):
            data = f"<!doctype html><title>Fake sign-in</title><body style='font:20px sans-serif'>{body}".encode()
            self.send_response(status)
            if location:
                self.send_header("Location", location)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urllib.parse.urlsplit(self.path)
            q = dict(urllib.parse.parse_qsl(u.query))
            if u.path == "/authorize":
                back = q.get("redirect_uri", "")
                if mode == "never":
                    return self._send("Sign in to Fake. (This page never finishes.)")
                extra = "error=access_denied" if mode == "fail" else f"code={code}"
                target = f"{back}?{extra}&state={urllib.parse.quote(q.get('state', ''))}"
                return self._send(f"<meta http-equiv='refresh' content='{delay};url={target}'>Signing in to Fake…")
            if u.path == "/callback":
                if q.get("error"):
                    self._send("Sign-in failed.")
                    return finish(False, f"Login failed: {q['error']}")
                if q.get("state") != state or q.get("code") != code:
                    self._send("Invalid state or code.", 400)
                    return finish(False, "Login failed: Invalid state parameter")
                self._send("", 302, "/done")
                return finish(True, "Login successful.")
            if u.path == "/code":
                return self._send(f"Paste this code into the CLI: <b>{q.get('code', '')}#{q.get('state', '')}</b>")
            if u.path == "/done":
                return self._send("Signed in to Fake. You can close this tab.")
            self._send("Not found", 404)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    q = urllib.parse.quote
    auto = f"{base}/authorize?client_id=fake&redirect_uri={q(base + '/callback', safe='')}&state={state}"
    manual = f"{base}/authorize?client_id=fake&redirect_uri={q(base + '/code', safe='')}&state={state}"

    print("Opening browser to sign in…", flush=True)
    if mode != "manual" and os.environ.get("BROWSER"):
        subprocess.run([*shlex.split(os.environ["BROWSER"]), auto], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False)
    print(f"Browser didn't open? Use the url below to sign in:\n\n{manual}\n\n", flush=True)
    print("Paste code here if prompted > ", end="", flush=True)

    def read_code():
        for line in sys.stdin:
            got = line.strip()
            if got:
                if got == f"{code}#{state}":
                    return finish(True, "Login successful.")
                return finish(False, "Invalid code. Please make sure the full code was copied")
    threading.Thread(target=read_code, daemon=True).start()

    finished.wait(3600)
    print("\n" + outcome.get("said", "Timed out."), flush=True)
    if outcome.get("ok"):
        threading.Event().wait(1.0)   # let the browser fetch the "signed in" page it was sent to
    server.shutdown()
    return 0 if outcome.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

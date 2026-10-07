"""An imitation of Slack's app pages, for the Slack setup recipe's tests, in two forms that tell the same story: HTML a
real Chromium shows (`Site.render`) and `FakePage`s the fake debugging port shows (`Site.fake`).

The story, as the recipe expects Slack's to go: a "create an app" page with Create (or Next, then Create); the app's
Basic Information page with Install to Workspace near the top and, far down, App-Level Tokens with Generate Token and
Scopes, which opens a box with a name, Add Scope and Generate, after which the token shows in a field; a consent page
with Allow; and the OAuth & Permissions page showing the User OAuth Token in a field. With `wall`, the consent page is
instead the one that says an administrator must approve apps. All names are invented (workspace "Acme"), and the two
tokens are built here at run time, never written whole.
"""

from . import fake_cdp

APP_ID = "A0ACME1234"


def app_token() -> str:
    return "xap" + "p-" + "-".join(["1", APP_ID, "1234567890123", "ab12cd34" * 8])


def user_token() -> str:
    return "xox" + "p-" + "-".join(["1" * 12, "2" * 13, "3" * 13, "a1b2c3d4" * 4])


STYLE = ("<style>body{font:16px system-ui,sans-serif;margin:24px}button{font:inherit;padding:8px 16px;margin:6px 8px 6px 0}"
         "input{font:inherit;padding:6px;width:560px}.gap{height:1500px}</style>")


def _page(title: str, body: str, script: str = "") -> str:
    return f"<!doctype html><html><head><meta charset=utf-8><title>{title}</title>{STYLE}</head><body>{body}" \
           f"<script>{script}</script></body></html>"


class Site:
    def __init__(self, wall: bool = False, two_step: bool = False, token_in_text: bool = False):
        self.wall, self.two_step, self.token_in_text = wall, two_step, token_in_text
        self.app_token, self.user_token = app_token(), user_token()

    # -- HTML, for a real browser --

    def render(self, path: str, query: str) -> tuple[int, str]:
        if path == "/apps":
            return 200, self._create()
        if path == f"/apps/{APP_ID}/":
            return 200, self._basic()
        if path == "/oauth":
            return 200, self._wall() if self.wall else self._consent()
        if path == f"/apps/{APP_ID}/oauth":
            return 200, self._tokens()
        return 404, _page("Not found", "<h1>Not found</h1>")

    def creation_url(self, base: str) -> str:
        return f"{base}/apps?new_app=1&manifest_json=%7B%22display_information%22%3A%7B%22name%22%3A%22Bombadil%22%7D%7D"

    def _create(self) -> str:
        review = ("<h1>Review summary &amp; create your app</h1><p>Bombadil will have 10 user scopes.</p>"
                  f"<button onclick=\"location.href='/apps/{APP_ID}/'\">Create</button><button>Back</button>")
        if not self.two_step:
            return _page("Create app", review.replace("<button>Back</button>", "<button>Cancel</button>"))
        first = ("<h1>Create app from manifest</h1><p>Pick a workspace to develop your app in.</p>"
                 "<button onclick=\"document.getElementById('one').hidden=true;document.getElementById('two').hidden=false\">"
                 "Next</button><button>Cancel</button>")
        return _page("Create app", f"<div id=one>{first}</div><div id=two hidden>{review}</div>")

    def _basic(self) -> str:
        shown = (f"<div id=made hidden>Token Generated <input readonly value=\"{self.app_token}\"><button>Copy</button></div>"
                 if not self.token_in_text else
                 f"<div id=made hidden>Token Generated <code>{self.app_token}</code><button>Copy</button></div>")
        return _page("Basic Information", (
            "<h1>Basic Information</h1><h2>Install your app</h2>"
            "<p>Install your app to your workspace to generate tokens. Apps need approval from an administrator "
            "in some workspaces.</p>"
            "<button onclick=\"location.href='/oauth?client_id=1.2'\">Install to Workspace</button>"
            "<div class=gap></div><h2>App-Level Tokens</h2>"
            "<p>An app-level token lets your app use platform features that apply to all workspaces.</p>"
            "<button onclick=\"document.getElementById('box').hidden=false\">Generate Token and Scopes</button>"
            "<div id=box hidden><h3>Generate an app-level token</h3><label>Token Name <input id=name></label>"
            "<br><button>Add Scope</button>"
            "<button onclick=\"document.getElementById('made').hidden=false\">Generate</button>" + shown + "</div>"))

    def _consent(self) -> str:
        return _page("Permissions", (
            "<h1>Bombadil is requesting permission to access the Acme Slack workspace</h1>"
            "<button>Cancel</button>"
            f"<button onclick=\"location.href='/apps/{APP_ID}/oauth'\">Allow</button>"))

    def _wall(self) -> str:
        return _page("Request to install", (
            "<h1>Request to install Bombadil</h1><p>Installing apps in Acme needs approval from a workspace "
            "administrator.</p><button>Submit Request</button>"))

    def _tokens(self) -> str:
        return _page("OAuth & Permissions", (
            "<h1>OAuth &amp; Permissions</h1><h2>OAuth Tokens for Your Workspace</h2><p>User OAuth Token</p>"
            f"<input readonly value=\"{self.user_token}\"><button>Copy</button>"
            "<button>Reinstall to Workspace</button>"))

    # -- the same pages for the fake debugging port --

    def fake(self, chrome: fake_cdp.FakeChrome, base: str = "https://api.slack.test") -> None:
        """Make the stand-in's tabs open on this site's first page (whatever address /json/new is given)."""
        chrome.page_factory = lambda url: self._fake_create(chrome, base)

    def _go(self, chrome, page):
        return lambda tab_id: chrome.go(tab_id, page() if callable(page) else page)

    def _fake_create(self, chrome, base) -> fake_cdp.FakePage:
        E = fake_cdp.FakeElement
        review = fake_cdp.FakePage(
            f"{base}/apps?new_app=1", "Create app",
            "Review summary & create your app\nBombadil will have 10 user scopes.")
        review.elements = [E("Create", (700, 500, 90, 34), on_press=self._go(chrome, lambda: self._fake_basic(chrome, base))),
                           E("Cancel" if not self.two_step else "Back", (580, 500, 90, 34))]
        if not self.two_step:
            return review
        first = fake_cdp.FakePage(f"{base}/apps?new_app=1", "Create app",
                                  "Create app from manifest\nPick a workspace to develop your app in.")
        first.elements = [E("Next", (700, 500, 90, 34), on_press=self._go(chrome, review)), E("Cancel", (580, 500, 90, 34))]
        return first

    def _fake_basic(self, chrome, base) -> fake_cdp.FakePage:
        E = fake_cdp.FakeElement
        page = fake_cdp.FakePage(
            f"{base}/apps/{APP_ID}/", "Basic Information",
            "Basic Information\nInstall your app\nInstall your app to your workspace to generate tokens. Apps need "
            "approval from an administrator in some workspaces.\nApp-Level Tokens")

        def open_box(tab_id):
            page.text += "\nGenerate an app-level token\nToken Name"
            page.elements += [E("Add Scope", (60, 400, 100, 34)), E("Generate", (180, 400, 100, 34), on_press=reveal)]

        def reveal(tab_id):
            page.text += "\nToken Generated"
            if self.token_in_text:
                page.text += f"\n{self.app_token}"
            else:
                page.inputs.append(self.app_token)

        install = self._consent_page if not self.wall else self._wall_page
        page.elements = [E("Install to Workspace", (40, 200, 170, 34),
                           on_press=self._go(chrome, lambda: install(chrome, base))),
                         E("Generate Token and Scopes", (40, 700, 220, 34), on_press=open_box)]
        return page

    def _consent_page(self, chrome, base) -> fake_cdp.FakePage:
        E = fake_cdp.FakeElement
        page = fake_cdp.FakePage(f"{base}/oauth?client_id=1.2", "Permissions",
                                 "Bombadil is requesting permission to access the Acme Slack workspace")
        page.elements = [E("Cancel", (400, 500, 90, 34)),
                         E("Allow", (520, 500, 90, 34), on_press=self._go(chrome, lambda: self._fake_tokens(base)))]
        return page

    def _wall_page(self, chrome, base) -> fake_cdp.FakePage:
        E = fake_cdp.FakeElement
        page = fake_cdp.FakePage(f"{base}/oauth?client_id=1.2", "Request to install",
                                 "Request to install Bombadil\nInstalling apps in Acme needs approval from a workspace "
                                 "administrator.")
        page.elements = [E("Submit Request", (400, 500, 140, 34))]
        return page

    def _fake_tokens(self, base) -> fake_cdp.FakePage:
        E = fake_cdp.FakeElement
        page = fake_cdp.FakePage(f"{base}/apps/{APP_ID}/oauth", "OAuth & Permissions",
                                 "OAuth & Permissions\nOAuth Tokens for Your Workspace\nUser OAuth Token",
                                 inputs=[self.user_token])
        page.elements = [E("Copy", (700, 300, 70, 34)), E("Reinstall to Workspace", (40, 360, 200, 34))]
        return page

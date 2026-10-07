"""A place for the Slack setup recipe to be run in, and a "person" in it, on either backend: a real Chromium showing the
imitation pages, or the fake debugging port showing models of them (see slack_pages.py).

The person presses what Bombadil points at and nothing else (apart from the steps Bombadil does not point at). On
Chromium the person finds the pointer's ring in the page and clicks the middle of it with a real mouse event, so
whatever is drawn over the button is in the way if it takes clicks; on the fake port it presses the element the
pointer is on.
"""

import time

from bombadil import browser

from . import chromium, fake_cdp, slack_pages

POINTED = """(function () {
  var o = document.getElementById('bombadil-pointer');
  if (!o) return null;
  var r = o.getBoundingClientRect(), chip = o.firstChild;
  return {label: chip ? chip.textContent : '', x: r.left, y: r.top, w: r.width, h: r.height,
          visible: getComputedStyle(o).visibility};
})()"""

PRESS = """(function (text) {
  var all = document.querySelectorAll('button, a');
  for (var i = 0; i < all.length; i++) {
    var el = all[i], r = el.getBoundingClientRect();
    if (el.innerText.trim() === text && r.width > 0 && r.height > 0) {
      el.scrollIntoView({block: 'center'});
      r = el.getBoundingClientRect();
      return {x: r.left + r.width / 2, y: r.top + r.height / 2};
    }
  }
  return null;
})"""


def until(fn, timeout: float = 15.0, what: str = "something"):
    """Poll `fn` until it gives something other than None/False, for at most `timeout` seconds."""
    deadline = time.monotonic() + timeout
    while True:
        value = fn()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.03)


class FakeStage:
    kind = "fake"

    def __init__(self, site: slack_pages.Site):
        self.site = site
        self.chrome = fake_cdp.FakeChrome().start()
        site.fake(self.chrome)
        self.devtools = browser.DevTools(port=self.chrome.port)
        self.creation_url = "https://api.slack.test/apps?new_app=1&manifest_json=%7B%7D"

    def _tab(self) -> str | None:
        return next(iter(self.chrome.tabs), None)

    def pointed(self) -> dict | None:
        tab = self._tab()
        over = self.chrome.overlay(tab) if tab else None
        return None if over is None else {"label": over["label"], "rect": over["rect"],
                                          "target": over["element"].label}

    def press_pointed(self, label: str, timeout: float = 15.0) -> str:
        """Press what the pointer with this label is on; returns the words on what was pressed."""
        def ready():
            tab = self._tab()
            over = self.chrome.overlay(tab) if tab else None
            return over if over is not None and over["label"] == label else None
        over = until(ready, timeout, f"the pointer {label!r}")
        self.chrome.press(self._tab(), over["element"])
        return over["element"].label

    def press(self, text: str, timeout: float = 15.0) -> None:
        def ready():
            tab = self._tab()
            return next((e for e in self.chrome.page(tab).elements if e.label == text), None) if tab else None
        element = until(ready, timeout, f"the button {text!r}")
        self.chrome.press(self._tab(), element)

    def page_text(self) -> str:
        return self.chrome.page(self._tab()).text

    def close(self) -> None:
        self.chrome.stop()


class ChromiumStage:
    kind = "chromium"

    def __init__(self, browser_: chromium.Chromium, site: slack_pages.Site):
        self.site = site
        self.chromium = browser_
        self.devtools = browser_.devtools
        self.pages = chromium.Pages(site.render)
        self.creation_url = site.creation_url(self.pages.base)
        self._cdp: chromium.Cdp | None = None
        self._cdp_tab: str | None = None

    def cdp(self) -> chromium.Cdp:
        """A connection to the tab on this site (the newest), made again if the tab changed."""
        def tab():
            return next((t for t in self.devtools.tabs() if t["url"].startswith(self.pages.base)), None)
        found = until(tab, 20, "the page to be open")
        if self._cdp is None or found["id"] != self._cdp_tab:
            if self._cdp is not None:
                self._cdp.close()
            self._cdp, self._cdp_tab = chromium.Cdp(found["webSocketDebuggerUrl"]), found["id"]
        return self._cdp

    def pointed(self) -> dict | None:
        try:
            return self.cdp().evaluate(POINTED)
        except (RuntimeError, OSError, TimeoutError, AssertionError):
            return None

    def press_pointed(self, label: str, timeout: float = 15.0) -> str:
        def ready():
            now = self.pointed()
            return now if now and now["label"] == label and now["visible"] == "visible" else None
        now = until(ready, timeout, f"the pointer {label!r}")
        x, y = now["x"] + now["w"] / 2, now["y"] + now["h"] / 2
        words = self.cdp().evaluate(f"(function(){{var e=document.elementFromPoint({x},{y});"
                                    f"return e ? e.innerText || e.value || '' : ''}})()")
        self.cdp().click(x, y)
        return words.strip()

    def press(self, text: str, timeout: float = 15.0) -> None:
        def ready():
            return self.cdp().evaluate(f"({PRESS})({text!r})")
        at = until(ready, timeout, f"the button {text!r}")
        self.cdp().click(at["x"], at["y"])

    def page_text(self) -> str:
        return self.cdp().evaluate("document.body.innerText")

    def close(self) -> None:
        if self._cdp is not None:
            self._cdp.close()
        self.pages.stop()
        for tab in self.devtools.tabs():
            if tab["url"].startswith(self.pages.base) and len(self.devtools.tabs()) > 1:
                self.devtools.close(tab["id"])

"""Running the real service in a thread of the test, and a stand-in for the connection service's client."""

import asyncio
import threading

from bombadil.browserd import service as svc
from bombadil.connect import client as connect_client


class ServiceThread:
    """The browser service on an event loop of its own, so a test can use the blocking client as agentd does."""

    def __init__(self, service: svc.Service):
        self.service = service
        self.error: BaseException | None = None
        service.listening = threading.Event()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        self.loop = asyncio.new_event_loop()
        try:
            self.loop.run_until_complete(self.service.serve())
        except BaseException as e:  # noqa: BLE001 - kept for the test that started it
            self.error = e
            self.service.listening.set()
        finally:
            self.loop.close()

    def start(self) -> "ServiceThread":
        self._thread.start()
        if not self.service.listening.wait(10) or self.error is not None:
            raise RuntimeError(f"the service did not start: {self.error!r}")
        return self

    def stop(self) -> None:
        if self.loop is not None and self._thread.is_alive():
            self.loop.call_soon_threadsafe(self.service.stop)
        self._thread.join(10)

    def call(self, fn, *args):
        """Run `fn(*args)` on the service's loop and return what it returns (for a test that looks inside)."""
        done = threading.Event()
        box = {}

        def run():
            box["value"] = fn(*args)
            done.set()
        self.loop.call_soon_threadsafe(run)
        done.wait(5)
        return box.get("value")


class FakeConnect:
    """The connection service's client, as a module: `request(op, timeout, **args)`. Two of them can share `store`,
    one for the browser service (it stores secrets) and one for a caller (it asks for connections)."""

    ConnectError = connect_client.ConnectError
    ConnectUnavailable = connect_client.ConnectUnavailable

    def __init__(self, store: dict | None = None, state: str = "ok", name: str = "Acme"):
        self.store = {} if store is None else store
        self.calls: list[tuple[str, dict]] = []
        self.fail: Exception | None = None
        self.state, self.name = state, name
        self.id = "slack:w1"

    def request(self, op, timeout=2.0, **args):
        self.calls.append((op, dict(args)))
        if self.fail is not None:
            raise self.fail
        if op == "store_secret":
            self.store[args["name"]] = args["value"]
            return {"stored": True}
        if op == "connections":
            ready = {"app_token", "user_token"} <= set(self.store)
            state = self.state if ready else "setup"
            return {"connections": [{"id": self.id, "kind": "slack", "service": "slack",
                                     "name": self.name if state == "ok" else "", "state": state, "note": ""}]}
        raise self.ConnectError("That is not something the fake knows.", "bad_request")

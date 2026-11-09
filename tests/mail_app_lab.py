"""The mail service on the fake engine and a fake agentd, in a thread of their own: what the Mail window's tests run
the window against (test_mail_app_backend.py drives the backend with them, test_mail_app_qml.py the whole window).

Nothing here starts Thunderbird, asks a nameserver or leaves the paths the caller set (temp ones, in BOMBADIL_*
variables); every thread and socket is closed by `Lab.close`.
"""

import asyncio
import json
import threading
import time
import types
from pathlib import Path

from PySide6.QtCore import QEventLoop, QTimer

from bombadil.mail import accounts as accts
from bombadil.mail import fake, service
from bombadil.mail.service import Service

ROOT = Path(__file__).resolve().parents[1]
APP_PY = ROOT / "share" / "apps" / "mail" / "app.py"

WORK, HOME_ACCT, SCHOOL = "a1", "a2", "a3"
LAUNCH = "a1/launch-date-31@acme.example"            # Priya, asks for a date
SAM = "a1/pricing-copy-8@acme.example"
LEO = "a1/empty-state-2@acme.example"
HOSTILE = "a1/quota-notice-77@acme-support.example"  # "NOTICE TO THE AI ASSISTANT ..."
INVOICE = "a1/inv-f0907@brightline.example"          # with a PDF
NEWS = "a1/flow-41@flowweekly.example"               # HTML only
PHOTOS = "a2/photos-9@family.example"
UNKNOWN_LINE = "I can't tell whether that went. Look in Sent before you press Send again."

# the service's own clocks, made quick so that supervision and sends can be watched
CLOCKS = {"SUPERVISE_S": 0.05, "PUSH_S": 0.05, "BACKOFF_MIN": 0.05, "BACKOFF_MAX": 0.2, "SEND_S": 0.6}

_made = [0]


def scratch_env(home: Path, setenv) -> None:
    """Every path the service and the window use is in `home`. `setenv(name, value)` sets one (monkeypatch's, or
    os.environ's in a child)."""
    for name, value in {
        "BOMBADIL_MAIL_DB": home / "state" / "mail.db", "BOMBADIL_MAIL_FILES": home / "state" / "mail",
        "BOMBADIL_MAIL_PROFILE": home / "profile", "BOMBADIL_PRESS_LOG": home / "state" / "presses.jsonl",
        "BOMBADIL_MAIL_SOCKET": home / "run" / "mail.sock", "BOMBADIL_SOCKET": home / "run" / "agentd.sock",
    }.items():
        setenv(name, str(value))
    (home / "run").mkdir(parents=True, exist_ok=True)
    (home / "Downloads").mkdir(exist_ok=True)


def spin(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_until(cond, timeout: float = 6.0, what: str = "") -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        spin(15)
    assert cond(), f"waited {timeout} s for {what or getattr(cond, '__name__', 'a condition')}"
    return True


def holds_for(cond, seconds: float = 0.5) -> bool:
    """`cond()` stays true for this long."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not cond():
            return False
        spin(15)
    return True


def load_app():
    """app.py as the runtime loads it: compiled and run into a module of its own (nothing is written next to it)."""
    _made[0] += 1
    mod = types.ModuleType(f"mail_app_under_test_{_made[0]}")
    mod.__file__ = str(APP_PY)
    exec(compile(APP_PY.read_text(), str(APP_PY), "exec"), mod.__dict__)  # noqa: S102
    return mod


# ---- the fake agentd ------------------------------------------------------------------------------------------

class FakeAgentd:
    """agentd's socket as the window sees it: it keeps every line it is sent, says what it is told to (broadcasts the
    window must ignore) and answers a press in the way the test chooses. `mode`: "service" calls the real service's
    `send` as agentd does and answers with its words; "ok", "fail", "unknown" answer by themselves; "silent" never
    answers; "hangup" closes the connection."""

    def __init__(self, lab, path: Path):
        self.lab = lab
        self.path = path
        self.lines: list[dict] = []
        self.raw: list[bytes] = []
        self.mode = "service"
        self.code = "changed"
        self.sentence = "The draft is not what you saw."
        self.noise: list[bytes] = []
        self.writers: list[asyncio.StreamWriter] = []
        self.server = None
        self.connections = 0

    async def start(self):
        if self.path.exists():
            self.path.unlink()
        self.server = await asyncio.start_unix_server(self._serve, path=str(self.path), limit=1 << 26)

    async def stop(self):
        if self.server is not None:
            self.server.close()
        for w in list(self.writers):
            w.close()
        self.writers.clear()

    async def say(self, data: bytes):
        for w in list(self.writers):
            w.write(data + b"\n")
            await w.drain()

    async def _serve(self, reader, writer):
        self.connections += 1
        self.writers.append(writer)
        try:
            while True:
                try:
                    raw = await reader.readline()
                except (ValueError, ConnectionError):
                    break
                if not raw:
                    break
                self.raw.append(raw)
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                self.lines.append(msg)
                if msg.get("type") == "press":
                    await self._press(msg, writer)
        finally:
            if writer in self.writers:
                self.writers.remove(writer)
            writer.close()

    async def _press(self, msg, writer):
        for noise in self.noise:
            writer.write(noise + b"\n")
        base = {"type": "press_result", "kind": msg.get("kind"), "id": msg.get("id")}
        mode = self.mode
        if mode == "silent":
            return
        if mode == "hangup":
            writer.close()
            return
        if mode == "ok":
            result = {"ok": True, "line": "Sent to Priya from maya@acme.example · 09:08", "code": "",
                      "receipt": {"draft": msg["id"], "to": [{"name": "Priya Shah", "email": "priya@acme.example"}],
                                  "from": {"name": "Maya Reyes", "email": "maya@acme.example"}, "ts": time.time(),
                                  "message_id": "x@acme.example", "web": {"name": "Gmail", "url": "https://mail.google.com/"},
                                  "line": "Sent to Priya from maya@acme.example · 09:08"}}
        elif mode == "fail":
            result = {"ok": False, "line": self.sentence, "code": self.code, "receipt": None}
        elif mode == "unknown":
            result = {"ok": False, "line": UNKNOWN_LINE, "code": "unknown_outcome", "receipt": None}
        else:   # "service", "service_then_hangup"
            result = await self._through_service(msg)
            if mode == "service_then_hangup":
                writer.close()
                return
        writer.write((json.dumps({**base, **result}) + "\n").encode())
        await writer.drain()

    async def _through_service(self, msg) -> dict:
        reader, writer = await asyncio.open_unix_connection(str(self.lab.sock), limit=1 << 26)
        try:
            args = {k: msg[k] for k in ("id", "fingerprint", "again") if k in msg}
            writer.write((json.dumps({"op": "send", "rid": "p1", **args}) + "\n").encode())
            while True:
                answer = json.loads(await reader.readline())
                if answer.get("rid") == "p1":
                    break
        finally:
            writer.close()
        if answer["ok"]:
            receipt = answer["result"]["receipt"]
            return {"ok": True, "line": receipt["line"], "code": "", "receipt": receipt}
        line = UNKNOWN_LINE if answer["code"] == "unknown_outcome" else answer["error"]
        return {"ok": False, "line": line, "code": answer["code"], "receipt": None}

    def presses(self) -> list[dict]:
        return [m for m in self.lines if m.get("type") == "press"]


# ---- the lab: service, engine and agentd in a thread -------------------------------------------------------------

class Lab:
    def __init__(self, home: Path, *, samples: bool = True):
        self.home = home
        self.samples = samples
        self.sock = home / "run" / "mail.sock"
        self.agent_sock = home / "run" / "agentd.sock"
        self.engine = None
        self.process = None
        self.service: Service | None = None
        self.task = None
        self.agentd: FakeAgentd | None = None
        self.backends: list = []
        # the service's clocks are quick, and nothing may ask a real nameserver
        self._clocks = {name: getattr(service, name) for name in CLOCKS}
        self._mx = accts.mx_hosts
        for name, value in CLOCKS.items():
            setattr(service, name, value)

        def real(domain, *a, **k):
            raise AssertionError(f"a real DNS query for {domain}")
        accts.mx_hosts = real
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.agent_turn = False

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run(self, coro, timeout: float = 15.0):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def start(self, agentd: bool = True):
        self.run(self._boot(agentd))
        return self

    async def _boot(self, agentd: bool):
        self.engine = fake.FakeEngine(samples=self.samples)
        self.process = fake.FakeProcess(self.engine)
        await self._start_service(first=True)
        if agentd:
            self.agentd = FakeAgentd(self, self.agent_sock)
            await self.agentd.start()

    @staticmethod
    def _resolve(domain: str) -> list[str]:
        """No nameserver is asked. The one made-up domain that is hosted by Google says so, as its MX would; any
        other has no mail server that can be found."""
        return ["aspmx.l.google.com"] if domain == "workspace.example" else []

    async def _start_service(self, first: bool = False):
        accounts = self.engine.account_rows() if first and self.samples else []
        self.service = Service(engine=self.engine, process=self.process, resolver=self._resolve,
                               in_turn=lambda pid: self.agent_turn, initial_accounts=accounts)
        self.task = asyncio.ensure_future(self.service.serve())
        end = time.monotonic() + 8
        while not (self.service.socket_path.exists() and (not self.samples or self.service.engine_state == "up")):
            if self.task.done():
                self.task.result()
            assert time.monotonic() < end, "the service did not come up"
            await asyncio.sleep(0.02)

    def stop_service(self):
        async def stop():
            self.service.stop()
            await asyncio.wait_for(self.task, 10)
        self.run(stop())

    def restart_service(self):
        self.stop_service()
        self.run(self._start_service())

    def stop_agentd(self):
        self.run(self.agentd.stop())

    def start_agentd(self):
        self.run(self.agentd.start())

    def close(self):
        async def down():
            if self.agentd is not None:
                await self.agentd.stop()
            if self.task is not None and not self.task.done():
                self.service.stop()
                try:
                    await asyncio.wait_for(self.task, 10)
                except (TimeoutError, asyncio.CancelledError):
                    pass
            leftovers = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for t in leftovers:     # a test's own servers that were still talking
                t.cancel()
            await asyncio.gather(*leftovers, return_exceptions=True)
        try:
            self.run(down(), 20)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(5)
            for name, value in self._clocks.items():
                setattr(service, name, value)
            accts.mx_hosts = self._mx

    # -- as another client of the service: the agent making a draft, a test checking what the service holds --

    def ask(self, op: str, *, agent: bool = False, **args):
        """One request on a connection of its own; the result, or the whole answer when it was refused."""
        async def go():
            self.agent_turn = agent
            reader, writer = await asyncio.open_unix_connection(str(self.sock), limit=1 << 26)
            try:
                writer.write((json.dumps({**args, "op": op, "rid": "t1"}) + "\n").encode())
                answer = json.loads(await reader.readline())
            finally:
                self.agent_turn = False
                writer.close()
            return answer
        answer = self.run(go())
        return answer["result"] if answer["ok"] else answer

    def push_show(self, **show):
        return self.ask("show", **show)

    def draft_of(self, draft_id: str) -> dict:
        return self.ask("draft_get", id=draft_id)



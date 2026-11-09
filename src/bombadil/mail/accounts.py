"""Which provider an address belongs to, how Thunderbird reaches it, and where its web page is.

The person types one thing, an email address, and the rest comes from here: the well-known domains
first, then the domain's MX record (a company on Google Workspace or Microsoft 365 has its own
domain but Google's or Microsoft's mail servers), and failing both a guess at `imap.<domain>`.
Nothing here knows whether the person's workplace will let Thunderbird in: that is what the engine
reports once it tries, and the service shows it as the account's state.

The resolver is a small DNS-over-UDP client on the standard library, because nothing else in
Bombadil's services needs one and a query for one MX record is thirty lines. It never raises: a
question that got no answer is an empty list, and the address falls through to the generic guess.
"""

import random
import re
import socket
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from urllib.parse import quote

from . import protocol

MB = 1_000_000


@dataclass(frozen=True)
class Provider:
    key: str            # "google" | "microsoft" | "icloud" | "imap"
    name: str
    imap_host: str
    imap_port: int
    imap_security: str  # "ssl" | "starttls"
    smtp_host: str
    smtp_port: int
    smtp_security: str
    auth: str           # "oauth2" | "password"
    web_name: str       # what "Open in ..." says; empty when there is no web page to open
    web: str            # the mailbox's page; {email} is the address
    size_limit: int     # bytes of one message, as the provider counts them (encoded)
    note: str           # what the person is asked to do while the account signs in


GOOGLE = Provider("google", "Google", "imap.gmail.com", 993, "ssl", "smtp.gmail.com", 465, "ssl", "oauth2",
                  "Gmail", "https://mail.google.com/mail/u/{email}/", 25 * MB,
                  "Google asks you to allow Thunderbird to read and send your mail.")
MICROSOFT = Provider("microsoft", "Microsoft", "outlook.office365.com", 993, "ssl", "smtp.office365.com", 587,
                     "starttls", "oauth2", "Outlook", "https://outlook.office.com/mail/", 35 * MB,
                     "Microsoft asks you to allow Thunderbird to read and send your mail.")
ICLOUD = Provider("icloud", "iCloud", "imap.mail.me.com", 993, "ssl", "smtp.mail.me.com", 587, "starttls",
                  "password", "iCloud Mail", "https://www.icloud.com/mail", 20 * MB,
                  "iCloud needs an app-specific password, which you make at appleid.apple.com.")
IMAP = Provider("imap", "Other", "imap.{domain}", 993, "ssl", "smtp.{domain}", 587, "starttls", "password",
                "", "", 20 * MB, "Thunderbird will ask for your mail password.")
PROVIDERS = {p.key: p for p in (GOOGLE, MICROSOFT, ICLOUD, IMAP)}

_KNOWN = {"gmail.com": "google", "googlemail.com": "google", "msn.com": "microsoft",
          "icloud.com": "icloud", "me.com": "icloud", "mac.com": "icloud"}
_CONSUMER_MICROSOFT = re.compile(r"(?:outlook|hotmail|live)\.(?:com|[a-z]{2}|co\.[a-z]{2}|com\.[a-z]{2})")


def valid_email(text) -> bool:
    return protocol.valid_email(text)


def _domain(email: str) -> str:
    return email.rpartition("@")[2].strip().lower()


def consumer_microsoft(email: str) -> bool:
    """Outlook.com, Hotmail and Live addresses, whose web page is not the one a company's Microsoft 365 uses."""
    return _CONSUMER_MICROSOFT.fullmatch(_domain(email)) is not None


def detect(email: str, resolver: Callable[[str], list[str]] | None = None) -> Provider:
    """The provider for this address. `resolver(domain)` returns the domain's MX host names; it is
    asked only for a domain that is not well known, and its failure means "no answer"."""
    domain = _domain(email)
    key = "microsoft" if _CONSUMER_MICROSOFT.fullmatch(domain) else _KNOWN.get(domain)
    if key is None:
        try:
            hosts = (resolver or mx_hosts)(domain)
        except Exception:  # noqa: BLE001 - a resolver that fails is a resolver with no answer
            hosts = []
        key = _from_mx(hosts)
    return for_domain(PROVIDERS[key], domain)


def for_domain(provider: Provider, domain: str) -> Provider:
    """The generic guess has its host names made from the domain; the others are what they are."""
    return replace(provider, imap_host=provider.imap_host.format(domain=domain),
                   smtp_host=provider.smtp_host.format(domain=domain))


def _from_mx(hosts: list[str]) -> str:
    for host in hosts:
        host = str(host).lower().rstrip(".")
        if host in ("google.com", "googlemail.com") or host.endswith((".google.com", ".googlemail.com")):
            return "google"
        if host.endswith(".mail.protection.outlook.com"):
            return "microsoft"
    return "imap"


def web_link(provider: Provider | str, email: str, message_id: str | None = None) -> str | None:
    """The page that opens this mailbox on the web, and Gmail's can open one message by its Message-ID.
    None when the provider has no web page we know."""
    if isinstance(provider, str):
        provider = PROVIDERS[provider]
    if not provider.web:
        return None
    url = provider.web.format(email=quote(email, safe="@"))
    if provider.key == "microsoft" and consumer_microsoft(email):
        url = url.replace("outlook.office.com", "outlook.live.com")
    if provider.key == "google" and message_id:
        url += "#search/rfc822msgid%3A" + quote(message_id, safe="")
    return url


# -- MX records --

MX = 15
MAX_NAMES = 64   # pointers followed while reading one name: a loop of them is an answer that cannot be read


def mx_hosts(domain: str, timeout: float = 3.0, *, resolv_conf: str = "/etc/resolv.conf", port: int = 53) -> list[str]:
    """The domain's mail servers, best first, from the nameservers in resolv.conf. `timeout` is for all of
    it. Returns [] for no answer or an answer that could not be read."""
    try:
        name = domain.strip(".").encode("idna")
        query_id = random.randrange(1 << 16)
        packet = struct.pack(">HHHHHH", query_id, 0x0100, 1, 0, 0, 0) + _encode(name) + struct.pack(">HH", MX, 1)
        servers = _nameservers(resolv_conf)[:3]
        deadline = time.monotonic() + timeout
        for i, server in enumerate(servers):
            left = deadline - time.monotonic()
            if left <= 0:
                break
            answer = _ask(server, port, packet, left / (len(servers) - i))
            if answer is not None:
                return _mx_answer(answer, query_id)
    except (OSError, ValueError, struct.error, IndexError):
        pass
    return []


def _nameservers(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            rows = [line.split() for line in f]
    except OSError:
        return []
    return [r[1] for r in rows if len(r) >= 2 and r[0] == "nameserver"]


def _encode(name: bytes) -> bytes:
    out = b""
    for label in name.split(b"."):
        if not 0 < len(label) < 64:
            raise ValueError("not a domain name")
        out += bytes([len(label)]) + label
    return out + b"\0"


def _ask(server: str, port: int, packet: bytes, timeout: float) -> bytes | None:
    family = socket.AF_INET6 if ":" in server else socket.AF_INET
    deadline = time.monotonic() + timeout
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(packet, (server, port))
            answer = s.recv(4096)
        if len(answer) > 2 and answer[2] & 0x02:   # TC: it did not fit, ask again over TCP
            with socket.socket(family, socket.SOCK_STREAM) as s:
                s.settimeout(max(0.1, deadline - time.monotonic()))
                s.connect((server, port))
                s.sendall(struct.pack(">H", len(packet)) + packet)
                head = _recv(s, 2)
                answer = _recv(s, struct.unpack(">H", head)[0])
        return answer
    except (OSError, struct.error):
        return None


def _recv(s: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise OSError("closed")
        buf += chunk
    return buf


def _name(msg: bytes, pos: int) -> tuple[str, int]:
    """The name at `pos` (labels, possibly ending in a pointer), and where the next field starts."""
    labels, end, hops = [], None, 0
    while True:
        n = msg[pos]
        if n == 0:
            pos += 1
            break
        if n & 0xC0 == 0xC0:
            if end is None:
                end = pos + 2
            pos = ((n & 0x3F) << 8) | msg[pos + 1]
            hops += 1
            if hops > MAX_NAMES:
                raise ValueError("a name that points at itself")
            continue
        labels.append(msg[pos + 1:pos + 1 + n].decode("ascii", "replace"))
        pos += 1 + n
    return ".".join(labels), (pos if end is None else end)


def _mx_answer(msg: bytes, query_id: int) -> list[str]:
    ident, flags, qd, an, _ns, _ar = struct.unpack(">HHHHHH", msg[:12])
    if ident != query_id or not flags & 0x8000 or flags & 0x000F:
        return []   # not our answer, not an answer, or an error (no such domain)
    pos = 12
    for _ in range(qd):
        _, pos = _name(msg, pos)
        pos += 4
    found = []
    for _ in range(an):
        _, pos = _name(msg, pos)
        rtype, _cls, _ttl, size = struct.unpack(">HHIH", msg[pos:pos + 10])
        pos += 10
        if rtype == MX:
            (pref,) = struct.unpack(">H", msg[pos:pos + 2])
            host, _ = _name(msg, pos + 2)
            found.append((pref, host))
        pos += size
    return [host for _, host in sorted(found)]

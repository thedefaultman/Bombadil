"""mail/accounts.py: which provider an address belongs to, and the stdlib MX resolver (against a loopback DNS)."""

import socket
import struct
import threading
import time

import pytest

from bombadil.mail import accounts

REAL_MX_HOSTS = accounts.mx_hosts   # kept before any test replaces it


@pytest.fixture(autouse=True)
def no_real_dns(monkeypatch):
    """Nothing in this file may ask a real nameserver: a test that wants the resolver says where it is."""
    def real(domain, *a, **k):
        raise AssertionError(f"a real DNS query for {domain}")
    monkeypatch.setattr(accounts, "mx_hosts", real)


def hosts_of(domain):
    def resolver(d):
        assert d == domain, d
        return HOSTS[domain]
    return resolver


HOSTS: dict[str, list[str]] = {}


# -- detect --

@pytest.mark.parametrize("email, key", [
    ("maya@gmail.com", "google"), ("Maya@GoogleMail.com", "google"), ("a@icloud.com", "icloud"),
    ("a@me.com", "icloud"), ("a@outlook.com", "microsoft"), ("a@hotmail.co.uk", "microsoft"),
    ("a@live.com.au", "microsoft"), ("a@msn.com", "microsoft"),
])
def test_well_known_domains_are_known_without_asking_anyone(email, key):
    assert accounts.detect(email, resolver=lambda d: pytest.fail("the resolver was asked")).key == key


def test_a_company_on_google_workspace_or_microsoft_365_is_found_by_its_mail_servers():
    HOSTS["acme.example"] = ["alt1.aspmx.l.google.com.", "aspmx.l.google.com."]
    assert accounts.detect("maya@acme.example", resolver=hosts_of("acme.example")).key == "google"
    HOSTS["school.example"] = ["school-example.mail.protection.outlook.com."]
    assert accounts.detect("maya@school.example", resolver=hosts_of("school.example")).key == "microsoft"


def test_a_look_alike_mail_server_is_not_google_or_microsoft():
    for host in ("google.com.evil.example", "notgoogle.com", "mail.protection.outlook.com.evil.example",
                 "aspmx.l.google.com.attacker.example"):
        HOSTS["x.example"] = [host]
        assert accounts.detect("a@x.example", resolver=hosts_of("x.example")).key == "imap", host


def test_any_other_domain_is_a_generic_guess_with_its_own_host_names():
    p = accounts.detect("jo@family.example", resolver=lambda d: [])
    assert (p.key, p.imap_host, p.smtp_host, p.auth) == ("imap", "imap.family.example", "smtp.family.example",
                                                         "password")
    assert p.web_name == "" and accounts.web_link(p, "jo@family.example") is None


def test_a_resolver_that_fails_means_no_answer_not_an_error():
    def boom(d):
        raise OSError("no network")
    assert accounts.detect("jo@family.example", resolver=boom).key == "imap"


def test_a_company_domain_is_never_looked_up_for_a_well_known_one(monkeypatch):
    asked = []
    accounts.detect("a@gmail.com", resolver=asked.append)
    assert asked == []


def test_each_provider_says_how_to_reach_it():
    g, m, i = accounts.PROVIDERS["google"], accounts.PROVIDERS["microsoft"], accounts.PROVIDERS["icloud"]
    assert (g.imap_host, g.auth, g.size_limit) == ("imap.gmail.com", "oauth2", 25_000_000)
    assert (m.smtp_host, m.auth, m.size_limit) == ("smtp.office365.com", "oauth2", 35_000_000)
    assert (i.auth, i.web_name) == ("password", "iCloud Mail")
    assert all(p.note for p in accounts.PROVIDERS.values())


def test_the_email_check_is_the_protocols():
    assert accounts.valid_email("a@acme.example") and not accounts.valid_email("a@acme")


# -- web links --

def test_gmail_can_open_the_mail_itself_by_its_message_id():
    url = accounts.web_link("google", "maya@acme.example", "m+1/2@acme.example")
    assert url == "https://mail.google.com/mail/u/maya@acme.example/#search/rfc822msgid%3Am%2B1%2F2%40acme.example"
    assert accounts.web_link("google", "maya@acme.example") == "https://mail.google.com/mail/u/maya@acme.example/"


def test_outlook_dot_com_people_and_company_people_have_different_pages():
    assert accounts.web_link("microsoft", "a@outlook.com") == "https://outlook.live.com/mail/"
    assert accounts.web_link("microsoft", "a@school.example") == "https://outlook.office.com/mail/"
    assert accounts.web_link("microsoft", "a@school.example", "id") == "https://outlook.office.com/mail/"
    assert accounts.web_link("icloud", "a@icloud.com") == "https://www.icloud.com/mail"
    assert accounts.web_link("imap", "a@family.example") is None


def test_an_address_cannot_break_out_of_the_link():
    url = accounts.web_link("google", "x@acme.example", "id#frag?q=1&r=2 ")
    assert " " not in url and "?q" not in url and url.count("#") == 1


# -- the resolver, against a loopback DNS --

def name(n: str) -> bytes:
    return b"".join(bytes([len(p)]) + p.encode() for p in n.strip(".").split(".")) + b"\0"


def reply(query: bytes, answers: list[tuple[int, str]] | None = None, *, rcode: int = 0, tc: bool = False,
          ident: int | None = None, compress: bool = True) -> bytes:
    """A DNS answer to `query` with MX records (preference, host). The owner is a pointer to the question's
    name, and a host inside the question's domain ends in such a pointer too, as real servers write them."""
    qid = struct.unpack(">H", query[:2])[0] if ident is None else ident
    question = query[12:]
    domain = question[:question.index(b"\0") + 1]
    flags = 0x8180 | rcode | (0x0200 if tc else 0)
    body = b""
    for pref, host in answers or []:
        suffix = "." + domain_text(domain)
        if compress and host.endswith(suffix):
            exchange = name(host[:-len(suffix)])[:-1] + b"\xc0\x0c"
        else:
            exchange = name(host)
        rdata = struct.pack(">H", pref) + exchange
        body += b"\xc0\x0c" + struct.pack(">HHIH", 15, 1, 60, len(rdata)) + rdata
    return struct.pack(">HHHHHH", qid, flags, 1, len(answers or []), 0, 0) + question + body


def domain_text(wire: bytes) -> str:
    out, i = [], 0
    while wire[i]:
        out.append(wire[i + 1:i + 1 + wire[i]].decode())
        i += 1 + wire[i]
    return ".".join(out)


class Dns:
    """A nameserver on loopback: UDP and TCP on one port, answering with whatever `handler` makes of the query."""

    def __init__(self, handler, host="127.0.0.1", port=0, tcp_handler=None):
        self.handler = handler
        self.tcp_handler = tcp_handler or handler
        self.queries: list[tuple[str, bytes]] = []
        for _ in range(50):   # a free UDP port whose TCP twin is free as well
            self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp.bind((host, port))
            self.port = self.udp.getsockname()[1]
            self.tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                self.tcp.bind((host, self.port))
                break
            except OSError:
                self.udp.close()
                self.tcp.close()
                if port:
                    raise
        self.tcp.listen(4)
        self.udp.settimeout(0.1)
        self.tcp.settimeout(0.1)
        self.stop = False
        for target in (self._udp, self._tcp):
            threading.Thread(target=target, daemon=True).start()

    def _udp(self):
        while not self.stop:
            try:
                query, addr = self.udp.recvfrom(4096)
            except TimeoutError:
                continue
            except OSError:
                return
            self.queries.append(("udp", query))
            answer = self.handler(query)
            if answer is not None:
                self.udp.sendto(answer, addr)

    def _tcp(self):
        while not self.stop:
            try:
                conn, _ = self.tcp.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with conn:
                conn.settimeout(1)
                try:
                    (size,) = struct.unpack(">H", conn.recv(2))
                    query = conn.recv(size)
                except (OSError, struct.error):
                    continue
                self.queries.append(("tcp", query))
                answer = self.tcp_handler(query)
                if answer is not None:
                    conn.sendall(struct.pack(">H", len(answer)) + answer)

    def close(self):
        self.stop = True
        for s in (self.udp, self.tcp):
            s.close()


@pytest.fixture
def dns(tmp_path):
    servers = []

    def make(handler, **kw):
        server = Dns(handler, **kw)
        servers.append(server)
        return server

    yield make
    for s in servers:
        s.close()


def conf(tmp_path, *ips):
    path = tmp_path / "resolv.conf"
    path.write_text("# fixture\nsearch example.test\n" + "".join(f"nameserver {ip}\n" for ip in ips))
    return str(path)


def resolve(server, tmp_path, domain="acme.example", timeout=2.0, ips=("127.0.0.1",)):
    return REAL_MX_HOSTS(domain, timeout, resolv_conf=conf(tmp_path, *ips), port=server.port)


def test_the_mail_servers_come_back_best_first_with_their_names_read_through_pointers(dns, tmp_path):
    server = dns(lambda q: reply(q, [(20, "alt.aspmx.l.google.com"), (10, "mx1.acme.example"),
                                     (5, "aspmx.l.google.com")]))
    assert resolve(server, tmp_path) == ["aspmx.l.google.com", "mx1.acme.example", "alt.aspmx.l.google.com"]
    assert server.queries[0][0] == "udp"


def test_names_that_are_not_compressed_read_the_same(dns, tmp_path):
    server = dns(lambda q: reply(q, [(10, "mx1.acme.example")], compress=False))
    assert resolve(server, tmp_path) == ["mx1.acme.example"]


def test_a_truncated_answer_is_asked_again_over_tcp(dns, tmp_path):
    server = dns(lambda q: reply(q, [], tc=True), tcp_handler=lambda q: reply(q, [(10, "mx.acme.example")]))
    assert resolve(server, tmp_path) == ["mx.acme.example"]
    assert [kind for kind, _ in server.queries] == ["udp", "tcp"]


def test_the_question_asked_is_the_domains_mx_record(dns, tmp_path):
    server = dns(lambda q: reply(q, []))
    resolve(server, tmp_path, "School.Example.")
    query = server.queries[0][1]
    assert domain_text(query[12:]).lower() == "school.example"
    assert struct.unpack(">HH", query[-4:]) == (15, 1)   # MX, IN
    assert struct.unpack(">H", query[4:6])[0] == 1       # one question


@pytest.mark.parametrize("make", [
    lambda q: reply(q, [(10, "mx.acme.example")], ident=(struct.unpack(">H", q[:2])[0] + 1) % 65536),   # not ours
    lambda q: reply(q, [(10, "mx.acme.example")], rcode=3),                                              # no such domain
    lambda q: reply(q, [(10, "mx.acme.example")], rcode=2),                                              # server failure
    lambda q: b"\x00" * 5,
    lambda q: q,                                                                                        # a query, not an answer
    lambda q: reply(q, [(10, "mx.acme.example")])[:-3],                                                  # cut short
    lambda q: q[:2] + struct.pack(">HHHHH", 0x8180, 1, 1, 0, 0) + q[12:] + b"\xc0\x0c\x00\x0f\x00\x01\x00\x00\x00\x3c\x00\x06\x00\x0a\xc0\x1d",
], ids=["wrong id", "nxdomain", "servfail", "garbage", "echo", "cut short", "pointer loop"])
def test_an_answer_that_is_not_ours_or_not_good_is_no_answer(dns, tmp_path, make):
    assert resolve(dns(make), tmp_path) == []


def test_a_nameserver_that_never_answers_is_given_up_on_in_the_time_given(dns, tmp_path):
    server = dns(lambda q: None)
    started = time.monotonic()
    assert resolve(server, tmp_path, timeout=0.4) == []
    assert time.monotonic() - started < 2.0


def test_the_next_nameserver_is_asked_when_the_first_is_silent(dns, tmp_path):
    silent = dns(lambda q: None)
    second = dns(lambda q: reply(q, [(10, "mx.acme.example")]), host="127.0.0.2", port=silent.port)
    assert resolve(silent, tmp_path, timeout=3.0, ips=("127.0.0.1", "127.0.0.2")) == ["mx.acme.example"]
    assert second.queries and silent.queries


def test_no_nameserver_or_no_resolv_conf_is_no_answer(tmp_path):
    empty = tmp_path / "empty.conf"
    empty.write_text("# nothing\n")
    assert REAL_MX_HOSTS("acme.example", 0.3, resolv_conf=str(empty), port=9) == []
    assert REAL_MX_HOSTS("acme.example", 0.3, resolv_conf=str(tmp_path / "missing"), port=9) == []


def test_a_domain_that_cannot_be_asked_about_is_no_answer_and_no_exception(tmp_path):
    path = conf(tmp_path, "127.0.0.1")
    for bad in ("", "a" * 70 + ".example", "bad..domain", "\x00.example"):
        assert REAL_MX_HOSTS(bad, 0.3, resolv_conf=path, port=9) == []


def test_detect_finds_google_through_a_real_query_when_no_resolver_is_given(dns, tmp_path, monkeypatch):
    server = dns(lambda q: reply(q, [(1, "aspmx.l.google.com")]))
    monkeypatch.setattr(accounts, "mx_hosts", lambda d: REAL_MX_HOSTS(d, 2.0, resolv_conf=conf(tmp_path, "127.0.0.1"),
                                                                     port=server.port))
    assert accounts.detect("maya@acme.example").key == "google"

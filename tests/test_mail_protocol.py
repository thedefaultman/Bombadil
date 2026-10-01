"""mail/protocol.py: ids, addresses, canonical JSON and native-messaging frames."""

import io
import json
import struct

import pytest

from bombadil.mail import protocol
from bombadil.mail.protocol import Addr, BadId, FrameError

# -- ids --

def test_a_mail_id_is_the_account_and_the_key():
    assert protocol.msg_id("a1", "m-1@example.test") == "a1/m-1@example.test"
    assert protocol.split_id("a12/m-1@example.test") == ("a12", "m-1@example.test")


def test_a_key_may_hold_slashes_because_a_message_id_can():
    assert protocol.split_id("a1/fp:ab/cd/ef") == ("a1", "fp:ab/cd/ef")


@pytest.mark.parametrize("bad", [None, 5, b"a1/k", "", "a1", "a1/", "/k", "x1/k", "a/k", "a1b/k", "A1/k",
                                 "a1/" + "k" * 513, "a1/k\nx", "a1/k\x00", "a1/k\u2028x", "a" + "1" * 10 + "/k"])
def test_an_id_that_is_not_one_is_refused_before_any_key_is_taken_out(bad):
    with pytest.raises(BadId):
        protocol.split_id(bad)


def test_the_longest_key_is_accepted():
    assert protocol.split_id("a1/" + "k" * 512)[1] == "k" * 512


# -- addresses --

def test_a_bare_address_and_a_named_one_parse_and_are_lower_cased():
    assert protocol.parse_addr("Priya@Acme.Example") == Addr("", "priya@acme.example")
    assert protocol.parse_addr("Priya Shah <Priya@acme.example>") == Addr("Priya Shah", "priya@acme.example")


def test_an_address_dict_parses_and_a_bad_one_does_not():
    assert protocol.parse_addr({"name": "Sam", "email": " SAM@acme.example "}) == Addr("Sam", "sam@acme.example")
    assert protocol.parse_addr({"name": None, "email": "sam@acme.example"}) == Addr("", "sam@acme.example")
    assert protocol.parse_addr({"name": 3, "email": "sam@acme.example"}) is None
    assert protocol.parse_addr({"email": None}) is None


@pytest.mark.parametrize("bad", [
    "", "nobody", "a@b", "a@b.c", "two words@acme.example", "a@@acme.example", "<a@acme.example>@x.test",
    "a@acme.example, b@acme.example", 'a"b@acme.example', "a@acme.example\r\nBcc: evil@acme.example",
    "a\x00@acme.example", "a@-acme.example", "a@acme-.example", "a@acme..example", "a@.acme.example",
    "a@acme.example\u200b", "a\u202e@acme.example", "a@acme\u00a0.example", "a@acme.ex\ud800ample",
    "x" * 65 + "@acme.example", "a@" + "x" * 250 + ".example", None, 7,
])
def test_what_is_not_one_mailbox_is_not_an_address(bad):
    assert protocol.parse_addr(bad) is None


def test_two_mailboxes_are_not_one_address():
    assert protocol.parse_addr("a@acme.example, b@acme.example") is None


def test_a_name_with_a_line_break_or_control_characters_is_flattened_so_no_header_can_be_built_from_it():
    got = protocol.parse_addr({"name": "Eve\r\nBcc: evil@acme.example\x00", "email": "eve@acme.example"})
    assert got is not None and "\n" not in got.name and "\r" not in got.name and "\x00" not in got.name
    assert "\n" not in str(got) and "\r" not in str(got)


def test_a_name_with_a_lone_surrogate_cannot_make_a_frame_that_will_not_encode():
    got = protocol.parse_addr({"name": "a\ud800b", "email": "x@acme.example"})
    assert got is not None
    json.dumps(got.as_dict(), ensure_ascii=False).encode()   # would raise on a lone surrogate


def test_one_line_is_plain_text_on_one_line_without_what_hides_or_reorders_it():
    assert protocol.one_line("  a \t b\r\nc\u2028d\x00e  ") == "a b c d e"
    assert protocol.one_line("Pri\u200bya \u202eSah\u2066") == "Priya Sah"      # zero-width and direction marks go
    assert protocol.one_line("a\ud800b") == "a b"                            # no lone surrogate
    assert protocol.one_line(None) == "" and protocol.one_line(12) == "12"
    assert protocol.one_line("x" * 500, 40) == "x" * 40
    assert len(protocol.one_line("y" * 5000)) == protocol.MAX_NAME


def test_parse_addrs_takes_header_style_strings_and_lists():
    got = protocol.parse_addrs('"Shah, Priya" <priya@acme.example>, sam@acme.example')
    assert got == [Addr("Shah, Priya", "priya@acme.example"), Addr("", "sam@acme.example")]
    assert protocol.parse_addrs(["a@acme.example", {"name": "B", "email": "b@acme.example"}]) == [
        Addr("", "a@acme.example"), Addr("B", "b@acme.example")]
    assert protocol.parse_addrs(None) == [] and protocol.parse_addrs("") == []


def test_one_bad_mailbox_spoils_the_list_and_is_named():
    with pytest.raises(ValueError, match="nobody"):
        protocol.parse_addrs(["a@acme.example", "nobody"])
    with pytest.raises(ValueError, match="bad@"):
        protocol.parse_addrs("a@acme.example, bad@")
    with pytest.raises(ValueError):
        protocol.parse_addrs(["a@acme.example", 5])
    with pytest.raises(ValueError):
        protocol.parse_addrs(["a@acme.example"] * 201)
    with pytest.raises(ValueError):
        protocol.parse_addrs(5)


def test_a_huge_or_control_laden_address_string_is_refused_not_parsed():
    with pytest.raises(ValueError):
        protocol.parse_addrs(["a@acme.example\x00"])
    with pytest.raises(ValueError):
        protocol.parse_addrs(["a@acme.example, " * 10_000])


def test_addr_or_raw_always_gives_something_a_list_can_show():
    assert protocol.addr_or_raw("Sam <sam@acme.example>") == Addr("Sam", "sam@acme.example")
    raw = protocol.addr_or_raw("undisclosed-recipients:;")
    assert raw.email == "undisclosed-recipients:;" and raw.name == ""
    assert protocol.addr_or_raw(None) == Addr("", "")
    assert protocol.addr_or_raw({"email": "no at sign\nhere"}).email == "no at sign here"


def test_format_addr_quotes_a_name_that_needs_it():
    assert protocol.format_addr(Addr("", "a@acme.example")) == "a@acme.example"
    assert protocol.format_addr(Addr("Priya Shah", "p@acme.example")) == "Priya Shah <p@acme.example>"
    assert protocol.format_addr(Addr("Shah, Priya", "p@acme.example")) == '"Shah, Priya" <p@acme.example>'
    assert protocol.parse_addr(str(Addr("Shah, Priya", "p@acme.example"))) == Addr("Shah, Priya", "p@acme.example")


def test_dedupe_keeps_the_first_spelling_of_each_mailbox():
    got = protocol.dedupe([Addr("Sam", "s@acme.example"), Addr("", "p@acme.example"), Addr("Sammy", "s@acme.example")])
    assert got == [Addr("Sam", "s@acme.example"), Addr("", "p@acme.example")]


# -- JSON --

def test_canonical_json_is_one_spelling_whatever_the_key_order():
    a = protocol.canonical({"b": 1, "a": [1, {"d": 1, "c": 2}], "é": "ü"})
    b = protocol.canonical({"é": "ü", "a": [1, {"c": 2, "d": 1}], "b": 1})
    assert a == b == '{"a":[1,{"c":2,"d":1}],"b":1,"é":"ü"}'


def test_canonical_json_tells_apart_what_a_fingerprint_must():
    assert protocol.canonical({"a": "1"}) != protocol.canonical({"a": 1})
    assert protocol.canonical({"a": ["x", "y"]}) != protocol.canonical({"a": ["y", "x"]})


# -- native messaging --

def frame(obj: dict) -> bytes:
    data = json.dumps(obj).encode()
    return struct.pack("=I", len(data)) + data


def test_a_frame_goes_out_and_comes_back():
    out = io.BytesIO()
    protocol.nm_write(out, {"op": "ping", "n": 1, "t": "é"})
    assert out.getvalue()[:4] == struct.pack("=I", len(out.getvalue()) - 4)
    assert protocol.nm_read(io.BytesIO(out.getvalue())) == {"op": "ping", "n": 1, "t": "é"}


def test_frames_come_one_at_a_time_and_a_clean_end_is_none():
    stream = io.BytesIO(frame({"a": 1}) + frame({"b": 2}))
    assert protocol.nm_read(stream) == {"a": 1}
    assert protocol.nm_read(stream) == {"b": 2}
    assert protocol.nm_read(stream) is None


class Dribble(io.RawIOBase):
    """A pipe that hands over one byte per read, the way a slow writer would."""

    def __init__(self, data: bytes):
        self.data, self.at = data, 0

    def read(self, n=-1):
        chunk = self.data[self.at:self.at + 1]
        self.at += len(chunk)
        return chunk


def test_a_frame_that_arrives_in_pieces_is_put_together():
    assert protocol.nm_read(Dribble(frame({"x": "y" * 50}))) == {"x": "y" * 50}


def test_a_frame_cut_short_is_an_error_and_not_a_clean_end():
    whole = frame({"a": 1})
    for cut in (1, 3, 5, len(whole) - 1):
        with pytest.raises(FrameError):
            protocol.nm_read(io.BytesIO(whole[:cut]))


def test_a_frame_over_the_limit_is_refused_before_it_is_read():
    stream = io.BytesIO(struct.pack("=I", protocol.NM_MAX_READ + 1) + b"x" * 10)
    with pytest.raises(FrameError, match="limit"):
        protocol.nm_read(stream)
    assert stream.tell() == 4   # only the length was read


@pytest.mark.parametrize("body", [b"not json", b"[1, 2]", b'"text"', b"5", b"null", b"", b'{"a":'])
def test_a_frame_that_is_not_a_json_object_is_an_error(body):
    with pytest.raises(FrameError):
        protocol.nm_read(io.BytesIO(struct.pack("=I", len(body)) + body))


def test_deeply_nested_json_is_an_error_and_not_a_crash():
    body = b"[" * 100_000 + b"]" * 100_000
    with pytest.raises(FrameError):
        protocol.nm_read(io.BytesIO(struct.pack("=I", len(body)) + body))


def test_log_is_one_line_on_stderr(capsys):
    protocol.log("two\nlines\tof   text")
    err = capsys.readouterr().err
    assert err == "bombadil-mail: two lines of text\n"


def test_a_refusal_carries_its_code_and_says_its_sentence():
    e = protocol.Refusal(protocol.CHANGED, "That is not the draft as it is now.")
    assert e.code == "changed" and str(e) == "That is not the draft as it is now."
    assert {protocol.CHANGED, protocol.UNKNOWN_OUTCOME, protocol.INTERNAL} <= set(protocol.CODES)

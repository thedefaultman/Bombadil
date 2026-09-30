"""mail/drafts.py: the fingerprint, the warnings, and the attachment copies the press depends on."""

import hashlib
import os
import stat

import pytest

from bombadil import paths
from bombadil.mail import accounts, drafts
from bombadil.mail.protocol import CHANGED, NOT_FOUND, REFUSED, TOO_BIG, Refusal

GOOGLE = accounts.PROVIDERS["google"]
IMAP = accounts.PROVIDERS["imap"]
PEM = b"-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----\n"


def attachment(name="a.pdf", size=3, sha="ab" * 32, **kw):
    return {"name": name, "size": size, "sha256": sha, **kw}


def draft(**kw):
    d = {"account": "a1", "kind": "reply", "reply_to": "a1/k1",
         "to": [{"name": "Priya", "email": "priya@acme.example"}], "cc": [], "bcc": [], "subject": "Re: Launch",
         "body": "The 14th.", "attachments": [], "tainted": False, "typed": [], "state": "open"}
    d.update(kw)
    return d


# -- the fingerprint --

def test_a_fingerprint_is_a_sha256_and_is_the_same_each_time():
    fp = drafts.fingerprint(draft())
    assert len(fp) == 64 and int(fp, 16) >= 0
    assert fp == drafts.fingerprint(draft())


@pytest.mark.parametrize("change", [
    {"account": "a2"}, {"kind": "reply_all"}, {"reply_to": "a1/k2"}, {"reply_to": None},
    {"to": [{"name": "Priya", "email": "priya@acme.example"}, {"name": "", "email": "sam@acme.example"}]},
    {"to": [{"name": "Priya", "email": "other@acme.example"}]}, {"to": [{"name": "P", "email": "priya@acme.example"}]},
    {"cc": [{"name": "", "email": "sam@acme.example"}]}, {"bcc": [{"name": "", "email": "sam@acme.example"}]},
    {"subject": "Re: Launch!"}, {"body": "The 15th."}, {"body": "The 14th. "},
    {"attachments": [attachment()]},
])
def test_anything_a_person_would_want_to_have_seen_changes_the_fingerprint(change):
    assert drafts.fingerprint(draft(**change)) != drafts.fingerprint(draft())


@pytest.mark.parametrize("change", [
    {"name": "b.pdf"}, {"size": 4}, {"sha": "cd" * 32},
])
def test_each_part_of_an_attachment_counts(change):
    a = drafts.fingerprint(draft(attachments=[attachment()]))
    b = drafts.fingerprint(draft(attachments=[attachment(**change)]))
    assert a != b


def test_the_order_of_recipients_and_of_attachments_counts():
    p, s = {"name": "", "email": "p@acme.example"}, {"name": "", "email": "s@acme.example"}
    assert drafts.fingerprint(draft(to=[p, s])) != drafts.fingerprint(draft(to=[s, p]))
    x, y = attachment("x.pdf"), attachment("y.pdf")
    assert drafts.fingerprint(draft(attachments=[x, y])) != drafts.fingerprint(draft(attachments=[y, x]))


@pytest.mark.parametrize("change", [
    {"state": "sending"}, {"tainted": True}, {"typed": ["x@acme.example"]}, {"warnings": [{"kind": "big"}]},
    {"created_by": "person"}, {"updated": 5.0}, {"shown_fp": "x"},
])
def test_what_is_not_content_does_not_change_the_fingerprint(change):
    assert drafts.fingerprint(draft(**change)) == drafts.fingerprint(draft())


def test_a_flag_on_an_attachment_is_not_content_either():
    assert drafts.fingerprint(draft(attachments=[attachment(sensitive=True)])) == \
        drafts.fingerprint(draft(attachments=[attachment(sensitive=False)]))


def test_text_that_would_not_encode_still_has_a_fingerprint_and_a_different_one():
    a, b = drafts.fingerprint(draft(body="x\ud800y")), drafts.fingerprint(draft(body="x\ud801y"))
    assert a != b and len(a) == 64


def test_a_body_that_looks_like_json_cannot_pass_for_another_field():
    a = draft(subject='a","body":"x', body="y")
    b = draft(subject="a", body='x","body":"y')
    assert drafts.fingerprint(a) != drafts.fingerprint(b)


# -- words --

@pytest.mark.parametrize("kind, subject, expected", [
    ("reply", "Launch", "Re: Launch"), ("reply", "Re: Launch", "Re: Launch"), ("reply", "RE: Launch", "RE: Launch"),
    ("reply_all", "re:Launch", "re:Launch"), ("reply", "AW: Launch", "AW: Launch"), ("reply", "Re[2]: x", "Re[2]: x"),
    ("reply", "Research plan", "Re: Research plan"), ("reply", "Regarding x", "Re: Regarding x"),
    ("forward", "Launch", "Fwd: Launch"), ("forward", "Fwd: Launch", "Fwd: Launch"), ("forward", "FW: x", "FW: x"),
    ("new", "Launch", "Launch"), ("new", "Re: Launch", "Re: Launch"), ("reply", "", "Re: "),
])
def test_a_subject_gets_its_prefix_once(kind, subject, expected):
    assert drafts.subject_for(kind, subject) == expected


def test_what_the_provider_adds_is_said_per_provider_and_kind():
    assert "Gmail signature" in drafts.adds("google", "new") and "quoted" not in drafts.adds("google", "new")
    assert "Outlook signature" in drafts.adds("microsoft", "reply") and "quoted" in drafts.adds("microsoft", "reply")
    assert drafts.adds("imap", "forward").startswith("Your signature, if you have one,")


# -- warnings --

def warn(d=None, *, trusted=(), known=(), provider=GOOGLE, origin=None):
    return drafts.warnings(d or draft(), trusted=set(trusted), known=set(known), provider=provider, origin=origin)


def test_a_draft_to_people_in_the_thread_has_nothing_to_say():
    assert warn(trusted={"priya@acme.example"}) == []


def test_an_address_the_engine_knows_is_not_new():
    assert warn(known={"priya@acme.example"}) == []


def test_an_address_nobody_explains_is_said_plainly_when_the_person_wrote_the_draft():
    [w] = warn()
    assert w["kind"] == "new_address" and w["addresses"] == ["priya@acme.example"]
    assert "New address: priya@acme.example" in w["text"] and "mail that was read for you" not in w["text"]


def test_it_is_said_more_strongly_when_what_read_mail_could_have_asked_for_it():
    [w] = warn(draft(tainted=True, to=[{"name": "", "email": "collect@outside.example"}]))
    assert "collect@outside.example" in w["text"] and "You did not type it" in w["text"]
    assert "mail that was read for you may have asked for it" in w["text"]


def test_several_new_addresses_are_listed_and_counted_and_bcc_counts():
    d = draft(to=[{"name": "", "email": f"u{i}@outside.example"} for i in range(5)],
              bcc=[{"name": "", "email": "hidden@outside.example"}], tainted=True)
    [w] = warn(d)
    assert len(w["addresses"]) == 6 and "hidden@outside.example" in w["addresses"]
    assert "New addresses: u0@outside.example, u1@outside.example, u2@outside.example and 3 more" in w["text"]
    assert "Check who they are" in w["text"]


def test_only_the_new_ones_are_said_when_some_are_trusted():
    d = draft(to=[{"name": "", "email": "priya@acme.example"}, {"name": "", "email": "evil@outside.example"}])
    [w] = warn(d, trusted={"priya@acme.example"})
    assert w["addresses"] == ["evil@outside.example"]


def test_a_reply_that_the_mail_asked_to_send_elsewhere_is_said():
    d = draft(to=[{"name": "", "email": "replies@elsewhere.example"}])
    origin = {"from": "priya@acme.example", "reply_to": ["replies@elsewhere.example"]}
    kinds = [w["kind"] for w in warn(d, trusted={"replies@elsewhere.example"}, origin=origin)]
    assert kinds == ["other"]
    [w] = warn(d, trusted={"replies@elsewhere.example"}, origin=origin)
    assert "replies@elsewhere.example" in w["text"] and "priya@acme.example" in w["text"]


def test_a_reply_to_the_sender_themselves_is_not_a_redirect():
    origin = {"from": "priya@acme.example", "reply_to": ["priya@acme.example"]}
    assert warn(trusted={"priya@acme.example"}, origin=origin) == []


def test_a_new_mail_is_not_a_redirect_whatever_the_origin_says():
    d = draft(kind="new", reply_to=None, to=[{"name": "", "email": "replies@elsewhere.example"}])
    origin = {"from": "priya@acme.example", "reply_to": ["replies@elsewhere.example"]}
    assert warn(d, trusted={"replies@elsewhere.example"}, origin=origin) == []


def test_a_file_that_looks_like_a_secret_is_said():
    d = draft(attachments=[attachment("id_rsa", sensitive=True), attachment("ok.pdf")])
    [w] = warn(d, trusted={"priya@acme.example"})
    assert w["kind"] == "sensitive_file" and "id_rsa" in w["text"]


def test_a_message_over_the_providers_limit_is_said_with_the_limit():
    big = attachment("big.zip", size=30_000_000)
    [w] = warn(draft(attachments=[big]), trusted={"priya@acme.example"})
    assert w["kind"] == "big" and "25 MB" in w["text"] and "Gmail" in w["text"]
    assert warn(draft(attachments=[big]), trusted={"priya@acme.example"}, provider=accounts.PROVIDERS["microsoft"]) == []
    [w] = warn(draft(attachments=[attachment("b.zip", size=22_000_000)]), trusted={"priya@acme.example"}, provider=IMAP)
    assert "your mail provider" in w["text"]


def test_the_encoded_size_is_the_files_grown_for_mail_plus_the_words():
    assert drafts.encoded_size(draft(attachments=[attachment(size=1_000_000)], body="")) == 1_370_000
    assert drafts.encoded_size(draft(body="é" * 10)) == 20


# -- attachment copies --

@pytest.fixture
def files(home, monkeypatch):
    monkeypatch.setenv("BOMBADIL_MAIL_FILES", str(home / "mailfiles"))
    (home / "docs").mkdir()
    return home / "docs"


def put(files, name, data=b"hello"):
    path = files / name
    path.write_bytes(data)
    return path


def test_a_copy_is_made_hashed_as_it_goes_and_described(files):
    src = put(files, "report.pdf", b"%PDF fixture" * 1000)
    got = drafts.copy_attachment("d1", src, None, "person")
    assert got == {"name": "report.pdf", "size": 12_000, "sha256": hashlib.sha256(b"%PDF fixture" * 1000).hexdigest(),
                   "sensitive": False}
    copy = paths.mail_files() / "drafts" / "d1" / "report.pdf"
    assert copy.read_bytes() == src.read_bytes()
    assert stat.S_IMODE(copy.stat().st_mode) == 0o600 and stat.S_IMODE(copy.parent.stat().st_mode) == 0o700


def test_an_empty_file_is_a_file(files):
    got = drafts.copy_attachment("d1", put(files, "empty.txt", b""), None, "person")
    assert got["size"] == 0 and got["sha256"] == hashlib.sha256(b"").hexdigest()


def test_the_copy_is_not_the_original_so_changing_the_original_later_changes_nothing(files):
    src = put(files, "a.txt", b"one")
    got = drafts.copy_attachment("d1", src, None, "agent")
    src.write_bytes(b"two")
    [data] = drafts.read_verified("d1", [got])
    assert data == b"one"


def test_two_files_of_one_name_both_come_along(files):
    (files / "x").mkdir()
    a = drafts.copy_attachment("d1", put(files, "a.txt", b"1"), None, "person")
    b = drafts.copy_attachment("d1", put(files / "x", "a.txt", b"2"), None, "person")
    assert (a["name"], b["name"]) == ("a.txt", "a (1).txt")
    assert [x for x in drafts.read_verified("d1", [a, b])] == [b"1", b"2"]


def test_a_name_given_is_used_and_is_made_safe(files):
    src = put(files, "a.txt")
    got = drafts.copy_attachment("d1", src, "../../../escape.txt", "person")
    folder = paths.mail_files() / "drafts" / "d1"
    assert (folder / got["name"]).exists() and "/" not in got["name"] and ".." not in got["name"].split(".")[0]
    names = sorted(str(p.relative_to(files.parent)) for p in files.parent.rglob("*") if p.is_file())
    assert not any(n.endswith("escape.txt") and not n.startswith("mailfiles/drafts/d1/") for n in names)


@pytest.mark.parametrize("given", ["/etc/passwd", "../../etc/passwd", "a/../../b", ".", "..", "", "CON", "x\x00y",
                                   "\u202e\u2066evil.txt", "-rf"])
def test_no_attachment_name_puts_a_file_anywhere_but_in_the_drafts_folder(files, home, given):
    src = put(files, "a.txt")
    before = {p for p in home.rglob("*") if p.is_file() and "mailfiles" not in p.parts}
    got = drafts.copy_attachment("d1", src, given, "person")
    after = {p for p in home.rglob("*") if p.is_file() and "mailfiles" not in p.parts}
    assert before == after
    copy = paths.mail_files() / "drafts" / "d1" / got["name"]
    assert copy.is_file() and copy.parent == paths.mail_files() / "drafts" / "d1"
    assert got["name"] and not got["name"].startswith((".", "-"))


def test_a_link_planted_in_the_drafts_folder_is_not_written_through(files, home):
    victim = files / "victim.txt"
    victim.write_text("precious")
    folder = paths.mail_files() / "drafts" / "d1"
    folder.mkdir(parents=True)
    os.symlink(victim, folder / "a.txt")
    os.symlink(files / "nowhere.txt", folder / "b.txt")
    got_a = drafts.copy_attachment("d1", put(files, "a.txt", b"new"), None, "person")
    got_b = drafts.copy_attachment("d1", put(files, "b.txt", b"new"), None, "person")
    assert victim.read_text() == "precious" and not (files / "nowhere.txt").exists()
    assert (got_a["name"], got_b["name"]) == ("a (1).txt", "b (1).txt")


def test_the_home_shorthand_is_understood_and_a_relative_path_is_not(files, home):
    put(files, "a.txt")
    assert drafts.copy_attachment("d1", "~/docs/a.txt", None, "person")["name"] == "a.txt"
    with pytest.raises(Refusal) as e:
        drafts.copy_attachment("d1", "docs/a.txt", None, "person")
    assert e.value.code == "bad_request"


def test_a_file_that_is_not_there_or_not_a_file_is_refused(files):
    with pytest.raises(Refusal) as e:
        drafts.copy_attachment("d1", files / "missing.pdf", None, "person")
    assert e.value.code == NOT_FOUND
    with pytest.raises(Refusal) as e:
        drafts.copy_attachment("d1", files, None, "person")
    assert e.value.code == REFUSED and "not a file" in str(e.value)


def test_a_pipe_is_refused_and_does_not_block(files):
    fifo = files / "pipe"
    os.mkfifo(fifo)
    with pytest.raises(Refusal) as e:
        drafts.copy_attachment("d1", fifo, None, "person")
    assert e.value.code == REFUSED


def test_a_device_is_refused_and_does_not_fill_the_disk():
    with pytest.raises(Refusal):
        drafts.copy_attachment("d1", "/dev/zero", None, "person")


def test_a_link_to_a_file_is_followed_to_the_file_and_a_link_to_a_secret_is_a_secret(files, home):
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_rsa").write_bytes(b"fixture")
    os.symlink(home / ".ssh" / "id_rsa", files / "holiday.jpg")
    with pytest.raises(Refusal) as e:
        drafts.copy_attachment("d1", files / "holiday.jpg", None, "agent")
    assert e.value.code == REFUSED
    person = drafts.copy_attachment("d1", files / "holiday.jpg", None, "person")
    assert person["sensitive"] is True and person["name"] == "id_rsa"   # named for what it is, not for its link


@pytest.mark.parametrize("name", ["id_rsa", "server.pem", "logins.json", ".env", "credentials.json"])
def test_an_agent_is_refused_a_secret_by_its_name_and_a_person_gets_it_flagged(files, name):
    src = put(files, name, b"fixture")
    with pytest.raises(Refusal) as e:
        drafts.copy_attachment("d1", src, None, "agent")
    assert e.value.code == REFUSED and name in str(e.value)
    assert not (paths.mail_files() / "drafts" / "d1").exists() or not list((paths.mail_files() / "drafts" / "d1").iterdir())
    assert drafts.copy_attachment("d1", src, None, "person")["sensitive"] is True


def test_a_private_key_with_an_innocent_name_is_found_by_its_content_and_nothing_is_left_behind(files):
    src = put(files, "notes.txt", b"some words\n" + PEM)
    src2 = put(files, "start.txt", PEM)
    folder = paths.mail_files() / "drafts" / "d1"
    with pytest.raises(Refusal) as e:
        drafts.copy_attachment("d1", src2, None, "agent")
    assert e.value.code == REFUSED and "private key" in str(e.value)
    assert not folder.exists() or list(folder.iterdir()) == []
    got = drafts.copy_attachment("d1", src2, None, "person")
    assert got["sensitive"] is True
    # A key that is not at the start of the file is found only within the first 8 KiB: stated limit of the sniff.
    assert drafts.copy_attachment("d2", src, None, "agent")["sensitive"] is True


def test_a_file_over_the_limit_is_refused_before_anything_is_copied(files, monkeypatch):
    monkeypatch.setattr(drafts, "ATTACHMENT_MAX", 10)
    with pytest.raises(Refusal) as e:
        drafts.copy_attachment("d1", put(files, "big.bin", b"x" * 11), None, "person")
    assert e.value.code == TOO_BIG
    assert not list((paths.mail_files() / "drafts").rglob("*.bin"))
    assert drafts.copy_attachment("d1", put(files, "ok.bin", b"x" * 10), None, "person")["size"] == 10


def test_a_file_that_grows_while_it_is_copied_is_cut_off_at_the_limit_and_leaves_nothing(files, monkeypatch):
    monkeypatch.setattr(drafts, "ATTACHMENT_MAX", 2000)
    monkeypatch.setattr(drafts, "CHUNK", 1000)
    src = put(files, "grow.bin", b"x" * 1500)
    real = os.fstat

    def lying(fd):
        st = real(fd)
        return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink, st.st_uid, st.st_gid, 10,
                               int(st.st_atime), int(st.st_mtime), int(st.st_ctime)))
    src.write_bytes(b"x" * 5000)
    monkeypatch.setattr(os, "fstat", lying)
    with pytest.raises(Refusal) as e:
        drafts.copy_attachment("d1", src, None, "person")
    assert e.value.code == TOO_BIG
    monkeypatch.undo()
    folder = paths.mail_files() / "drafts" / "d1"
    assert not folder.exists() or list(folder.iterdir()) == []


def test_a_draft_id_that_is_not_one_makes_no_folder(files):
    for bad in ("../x", "d1/../../x", "D1", "", "d", "d1x", "d" + "1" * 13, "/etc"):
        with pytest.raises(ValueError):
            drafts.folder(bad)
    assert drafts.folder("d12") == paths.mail_files() / "drafts" / "d12"


# -- read at send time --

def test_the_copies_are_read_back_checked_against_what_was_recorded(files):
    a = drafts.copy_attachment("d1", put(files, "a.txt", b"one"), None, "person")
    b = drafts.copy_attachment("d1", put(files, "b.txt", b"two"), None, "person")
    assert drafts.read_verified("d1", [a, b]) == [b"one", b"two"]
    assert drafts.read_verified("d1", []) == []


def folder_of(did="d1"):
    return paths.mail_files() / "drafts" / did


def test_a_copy_swapped_for_other_bytes_of_the_same_size_is_changed(files):
    a = drafts.copy_attachment("d1", put(files, "a.txt", b"one"), None, "person")
    (folder_of() / "a.txt").write_bytes(b"uno")
    with pytest.raises(Refusal) as e:
        drafts.read_verified("d1", [a])
    assert e.value.code == CHANGED and "a.txt" in str(e.value)


def test_a_copy_that_grew_or_shrank_or_went_is_changed(files):
    a = drafts.copy_attachment("d1", put(files, "a.txt", b"one"), None, "person")
    for content in (b"on", b"onetwo", b""):
        (folder_of() / "a.txt").write_bytes(content)
        with pytest.raises(Refusal) as e:
            drafts.read_verified("d1", [a])
        assert e.value.code == CHANGED
    (folder_of() / "a.txt").unlink()
    with pytest.raises(Refusal) as e:
        drafts.read_verified("d1", [a])
    assert e.value.code == CHANGED


def test_a_copy_replaced_by_a_link_to_a_file_that_has_the_same_bytes_is_still_changed(files):
    a = drafts.copy_attachment("d1", put(files, "a.txt", b"one"), None, "person")
    (folder_of() / "a.txt").unlink()
    os.symlink(put(files, "same.txt", b"one"), folder_of() / "a.txt")
    with pytest.raises(Refusal) as e:
        drafts.read_verified("d1", [a])
    assert e.value.code == CHANGED


def test_a_copy_replaced_by_a_pipe_or_a_folder_is_changed_and_does_not_block(files):
    a = drafts.copy_attachment("d1", put(files, "a.txt", b"one"), None, "person")
    (folder_of() / "a.txt").unlink()
    os.mkfifo(folder_of() / "a.txt")
    with pytest.raises(Refusal) as e:
        drafts.read_verified("d1", [a])
    assert e.value.code == CHANGED
    (folder_of() / "a.txt").unlink()
    (folder_of() / "a.txt").mkdir()
    with pytest.raises(Refusal) as e:
        drafts.read_verified("d1", [a])
    assert e.value.code == CHANGED


def test_a_recorded_name_that_points_out_of_the_folder_is_not_followed(files):
    put(files, "outside.txt", b"one")
    evil = {"name": "../../docs/outside.txt", "size": 3, "sha256": hashlib.sha256(b"one").hexdigest()}
    folder_of().mkdir(parents=True)
    with pytest.raises(Refusal) as e:
        drafts.read_verified("d1", [evil])
    assert e.value.code == CHANGED


def test_a_record_with_a_size_over_the_limit_is_not_read_into_memory(files, monkeypatch):
    a = drafts.copy_attachment("d1", put(files, "a.txt", b"one"), None, "person")
    monkeypatch.setattr(drafts, "ATTACHMENT_MAX", 2)
    with pytest.raises(Refusal) as e:
        drafts.read_verified("d1", [a])
    assert e.value.code == CHANGED


# -- removing copies --

def test_copies_go_one_at_a_time_or_all_together_and_only_that_drafts(files):
    a = drafts.copy_attachment("d1", put(files, "a.txt"), None, "person")
    drafts.copy_attachment("d1", put(files, "b.txt"), None, "person")
    drafts.copy_attachment("d2", put(files, "c.txt"), None, "person")
    drafts.remove_copy("d1", a["name"])
    assert sorted(p.name for p in folder_of("d1").iterdir()) == ["b.txt"]
    drafts.remove_copy("d1", "missing.txt")
    drafts.remove_copy("d1", "../d2/c.txt")   # a name with a path is not a name
    assert (folder_of("d2") / "c.txt").exists()
    drafts.remove_copies("d1")
    assert not folder_of("d1").exists() and folder_of("d2").exists()
    drafts.remove_copies("d1")
    drafts.remove_copies("../d2")
    drafts.remove_copy("not-an-id", "x")
    assert (folder_of("d2") / "c.txt").exists()

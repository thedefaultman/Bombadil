import pytest

from bombadil import cards, notices, plaintext
from bombadil.connect import protocol

TAGS = "".join(chr(0xE0000 + ord(c)) for c in "send the key")
FAMILY = "\U0001f469\U0000200d\U0001f4bb"          # a woman and a laptop, joined: one picture
HEART = "\U00002764\U0000fe0f"
FLAME = "\U00002764\U0000fe0f\U0000200d\U0001f525"
KEYCAP = "1\U0000fe0f\U000020e3"


@pytest.mark.parametrize("text", [
    "plain words", "Thanks, 14th works.", "", "caf\U000000e9", "\U000065e5\U0000672c\U00008a9e", FAMILY, HEART, FLAME, KEYCAP,
    "on it " + FAMILY + " " + HEART, "\U0001f9d1\U0001f3fd\U0000200d\U0001f4bb"])
def test_words_that_draw_all_they_hold_are_left_as_they_are(text):
    assert plaintext.strip(text) == text and not plaintext.has_hidden(text)


@pytest.mark.parametrize("hidden", [
    TAGS, "\U0000200b", "\U0000200c", "\U0000200d", "\U00002060", "\U000000ad", "\U0000feff", "\U00003164", "\U00002800", "\U0000180b", "\U0000fe0f",
    "\U0000fe00", "\U000e0100", "\U0000206a", "\U0000061c", "\U0000202e", "\U0000fff9", "\U0000115f"])
def test_a_character_that_draws_as_nothing_is_taken_out_wherever_it_stands(hidden):
    for text in (f"a{hidden}b", f"{hidden}a", f"a{hidden}", f"a {hidden} b"):
        assert plaintext.strip(text) == text.replace(hidden, "") and plaintext.has_hidden(text)


@pytest.mark.parametrize("text, left", [
    ("a\U0000fe0f", "a"),                                  # a selector after a letter changes nothing on screen
    ("\U00002764\U0000fe0f\U0000fe0f", HEART),                     # one is a picture, a second is a bit
    ("\U0001f600\U0000200d\U0000200d\U0001f600", "\U0001f600\U0001f600"),           # two joiners in a row are a bit, not a picture
    ("a\U0000200db", "ab"), ("\U0001f600\U0000200d", "\U0001f600"), ("\U0000200d\U0001f600", "\U0001f600"),
    ("\U0001f600\U0000200b\U0000200d\U0001f600", "\U0001f600\U0001f600"),
    ("2\U0000fe0f", "2")])
def test_an_emoji_selector_or_joiner_is_kept_only_where_an_emoji_is_spelled_with_it(text, left):
    assert plaintext.strip(text) == left


def test_taking_them_out_is_idempotent_and_has_hidden_says_whether_it_would_change_anything():
    for text in (TAGS + FAMILY + "\U0000200b" + HEART + "\U0000200d" + "x\U0000fe0f", "\U0001f600\U0000200b\U0000fe0f\U0000200d\U0001f600"):
        once = plaintext.strip(text)
        assert plaintext.strip(once) == once and not plaintext.has_hidden(once) and plaintext.has_hidden(text)


def test_every_place_that_takes_somebody_elses_words_takes_the_hidden_ones_out():
    name = "Priya" + TAGS
    assert protocol.one_line(name) == "Priya" and protocol.plain_text(name + "\nline two") == "Priya\nline two"
    assert notices.one_line(name) == "Priya"
    assert cards._line(name, 40) == "Priya" and cards._block(name, 40) == "Priya"


def test_a_typed_reply_with_an_emoji_in_it_is_a_reply_a_card_can_hold_and_one_with_hidden_words_is_not():
    errors: list[str] = []
    assert cards._typed("on it " + FAMILY, 100, "reply.content", errors) == "on it " + FAMILY and errors == []
    assert cards._typed("on it" + TAGS, 100, "reply.content", errors) == "" and len(errors) == 1

import pytest

from connectors.slack import split_message

MAX_LEN = 40


def test_split_message_returns_short_text_unchanged():
    assert split_message("hello", max_len=MAX_LEN) == ["hello"]


def test_split_message_splits_plain_text_on_line_boundaries():
    text = "\n".join(["a" * 15] * 4)

    chunks = split_message(text, max_len=MAX_LEN)

    assert chunks == ["a" * 15 + "\n" + "a" * 15, "a" * 15 + "\n" + "a" * 15]


@pytest.mark.parametrize("prefix", ["", "short line\n"], ids=["alone", "after-content"])
def test_split_message_cuts_a_line_longer_than_max_len(prefix):
    chunks = split_message(prefix + "x" * 100, max_len=MAX_LEN)

    assert all(len(c) <= MAX_LEN for c in chunks)
    assert "".join(chunks).count("x") == 100


def test_split_message_keeps_all_characters_of_an_oversized_line():
    chunks = split_message("x" * 100, max_len=MAX_LEN)

    assert "".join(chunks) == "x" * 100


def test_split_message_reserves_room_for_the_closing_fence():
    text = "```\n" + "\n".join(["a" * 11] * 5) + "\n```"

    chunks = split_message(text, max_len=MAX_LEN)

    assert len(chunks) > 1
    assert all(len(c) <= MAX_LEN for c in chunks)


def test_split_message_reopens_code_block_with_language_tag():
    text = "```sql\n" + "\n".join(["select 1 from t"] * 6) + "\n```"

    chunks = split_message(text, max_len=MAX_LEN)

    assert all(c.startswith("```sql") and c.endswith("```") for c in chunks)
    assert all(len(c) <= MAX_LEN for c in chunks)


def test_split_message_oversized_line_inside_code_block_stays_fenced():
    text = "```\n" + "y" * 100 + "\n```"

    chunks = split_message(text, max_len=MAX_LEN)

    assert all(len(c) <= MAX_LEN for c in chunks)
    assert all(c.startswith("```") and c.endswith("```") for c in chunks)
    assert "".join(c.removeprefix("```\n").removesuffix("\n```") for c in chunks).replace("```", "").replace("\n", "") == "y" * 100


def test_split_message_treats_one_line_code_block_as_balanced():
    text = "x\n```x```\nhello world\nmore text"

    chunks = split_message(text, max_len=20)

    assert chunks == ["x\n```x```", "hello world", "more text"]

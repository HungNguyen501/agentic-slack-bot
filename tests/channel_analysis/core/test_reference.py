import pytest

from channel_analysis.core.errors import ChannelAnalysisError
from channel_analysis.core.reference import ChannelReference, normalize_channel_name, parse_channel_reference


@pytest.mark.parametrize(
    "text, channel_id",
    [
        ("C0123456789", "C0123456789"),
        (" C0123456789 ", "C0123456789"),
        ("<#C0123456789|mail-reports>", "C0123456789"),
        ("<#C0123456789>", "C0123456789"),
        ("G0123456789", "G0123456789"),
    ],
)
def test_ids_and_channel_links_give_the_id(text, channel_id):
    assert parse_channel_reference(text) == ChannelReference(channel_id, None)


@pytest.mark.parametrize("text, name", [("#Mail-Reports", "mail-reports"), ("mail reports", "mail-reports"), ("  mail_reports ", "mail_reports")])
def test_a_name_is_normalized_the_way_slack_stores_it(text, name):
    assert parse_channel_reference(text) == ChannelReference(None, name)


@pytest.mark.parametrize("text", ["D0123456789", "<#D0123456789|dm>"])
def test_a_direct_message_is_refused(text):
    with pytest.raises(ChannelAnalysisError, match="direct message"):
        parse_channel_reference(text)


@pytest.mark.parametrize("text", ["", "   ", "#", None])
def test_an_empty_reference_asks_which_channel(text):
    with pytest.raises(ChannelAnalysisError, match="which channel"):
        parse_channel_reference(text)


def test_normalize_channel_name_drops_the_hash_and_joins_words():
    assert normalize_channel_name("#Data  Team") == "data-team"

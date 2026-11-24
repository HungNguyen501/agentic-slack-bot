import pytest

from channel_analysis.core.models import FileRef
from channel_analysis.infra import message_text


def test_a_plain_message_is_its_text():
    assert message_text.flatten_text({"text": "hello"}) == "hello"


def test_a_persons_message_is_not_repeated_by_its_rich_text_block():
    message = {
        "text": "see *this*",
        "blocks": [{"type": "rich_text", "elements": [{"type": "rich_text_section", "elements": [{"type": "text", "text": "see this"}]}]}],
    }

    assert message_text.flatten_text(message) == "see *this*"


def test_a_rich_text_body_longer_than_the_text_fallback_is_kept():
    message = {
        "text": "Daily ingestion report",
        "blocks": [
            {
                "type": "rich_text",
                "elements": [{"type": "rich_text_section", "elements": [{"type": "text", "text": "agility_po_costs skipped: no records"}]}],
            }
        ],
    }

    assert message_text.flatten_text(message) == "Daily ingestion report\nagility_po_costs skipped: no records"


def test_a_rich_text_block_that_is_only_part_of_a_word_of_the_text_is_kept():
    message = {
        "text": "Daily report",
        "blocks": [{"type": "rich_text", "elements": [{"type": "rich_text_section", "elements": [{"type": "text", "text": "port"}]}]}],
    }

    assert message_text.flatten_text(message) == "Daily report\nport"


def test_a_bot_report_in_blocks_and_attachments_is_flattened_in_order():
    message = {
        "text": "Mail Ingestion Report | bridgewell_com",
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "Report"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": "*agility_po_costs*: skipped (no records)"}, "fields": [{"type": "mrkdwn", "text": "rows: 0"}]},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "run 17:12"}]},
            {"type": "divider"},
        ],
        "attachments": [{"title": "Summary", "text": "1 of 12 skipped", "fields": [{"title": "failed", "value": "0"}]}],
    }

    assert message_text.flatten_text(message) == (
        "Mail Ingestion Report | bridgewell_com\nReport\n*agility_po_costs*: skipped (no records)\nrows: 0\nrun 17:12\nSummary\n1 of 12 skipped\nfailed: 0"
    )


def test_rich_text_without_a_text_field_is_read_with_lists_links_and_mentions():
    message = {
        "text": "",
        "blocks": [
            {
                "type": "rich_text",
                "elements": [
                    {
                        "type": "rich_text_section",
                        "elements": [
                            {"type": "user", "user_id": "U1"},
                            {"type": "text", "text": " check "},
                            {"type": "link", "url": "https://x.test/a", "text": "the doc"},
                            {"type": "emoji", "name": "eyes"},
                        ],
                    },
                    {"type": "rich_text_list", "elements": [{"type": "rich_text_section", "elements": [{"type": "text", "text": "one"}]}]},
                ],
            }
        ],
    }

    assert message_text.flatten_text(message) == "<@U1> check the doc (https://x.test/a):eyes:\n- one"


def test_a_report_made_of_blocks_inside_an_attachment_is_read():
    message = {
        "text": "",
        "attachments": [
            {
                "fallback": "Report",
                "blocks": [
                    {"type": "header", "text": {"type": "plain_text", "text": "Mail Ingestion Report"}},
                    {"type": "section", "text": {"type": "mrkdwn", "text": "agility_po_costs: skipped (no records)"}},
                ],
            }
        ],
    }

    assert message_text.flatten_text(message) == "Mail Ingestion Report\nagility_po_costs: skipped (no records)"


def test_a_table_block_gives_its_rows_with_raw_and_rich_text_cells():
    message = {
        "text": "",
        "blocks": [
            {
                "type": "table",
                "rows": [
                    [{"type": "raw_text", "text": "table"}, {"type": "raw_text", "text": "status"}],
                    [
                        {"type": "raw_text", "text": "agility_po_costs"},
                        {"type": "rich_text", "elements": [{"type": "rich_text_section", "elements": [{"type": "text", "text": "skipped"}]}]},
                    ],
                ],
            }
        ],
    }

    assert message_text.flatten_text(message) == "table | status\nagility_po_costs | skipped"


def test_a_table_next_to_a_short_fallback_text_keeps_its_data():
    message = {
        "text": "Daily report",
        "blocks": [{"type": "table", "rows": [[{"type": "raw_text", "text": "po_costs"}, {"type": "raw_text", "text": "0 rows"}]]}],
    }

    assert message_text.flatten_text(message) == "Daily report\npo_costs | 0 rows"


def rich(*elements):
    return {"text": "", "blocks": [{"type": "rich_text", "elements": [{"type": "rich_text_section", "elements": list(elements)}]}]}


def test_a_date_element_shows_its_fallback_text():
    message = rich({"type": "text", "text": "Deadline: "}, {"type": "date", "timestamp": 1800000000, "format": "{date_short}", "fallback": "Jan 15, 2027"})

    assert message_text.flatten_text(message) == "Deadline: Jan 15, 2027"


def test_a_date_element_without_a_fallback_shows_its_timestamp_in_utc():
    message = rich({"type": "text", "text": "Deadline: "}, {"type": "date", "timestamp": 1800000000, "format": "{date_short}"})

    assert message_text.flatten_text(message) == "Deadline: 2027-01-15 08:00 UTC"


@pytest.mark.parametrize(
    "element", [{"type": "date"}, {"type": "date", "timestamp": "soon"}, {"type": "date", "timestamp": 10**30}], ids=["no-timestamp", "word", "huge"]
)
def test_a_date_element_that_cannot_be_read_gives_nothing_instead_of_failing_the_message(element):
    assert message_text.flatten_text(rich({"type": "text", "text": "Due "}, element)) == "Due"


def test_an_attachment_with_only_a_fallback_uses_it():
    assert message_text.flatten_text({"attachments": [{"fallback": "PR #1 opened"}]}) == "PR #1 opened"


def test_the_same_text_in_text_and_a_block_appears_once():
    message = {"text": "same", "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "same"}}]}

    assert message_text.flatten_text(message) == "same"


@pytest.mark.parametrize(
    "message, author",
    [
        ({"username": "Mail Report", "bot_id": "B1"}, "Mail Report"),
        ({"bot_profile": {"name": "GitHub"}}, "GitHub"),
        ({"user": "U123"}, "<@U123>"),
        ({"bot_id": "B1"}, "bot"),
        ({}, "unknown"),
    ],
)
def test_author_of(message, author):
    assert message_text.author_of(message) == author


def test_reactions_and_files_are_read():
    message = {
        "reactions": [{"name": "white_check_mark", "count": 2}, {"count": 1}],
        "files": [{"id": "F1", "name": "rows.csv", "filetype": "csv", "size": 2048}, {"id": "F2", "mode": "tombstone"}, {"name": "no-id"}],
    }

    assert message_text.reactions_of(message) == (("white_check_mark", 2),)
    assert message_text.files_of(message) == (FileRef("F1", "rows.csv", "csv", 2048),)


@pytest.mark.parametrize("subtype, noise", [("channel_join", True), ("pinned_item", True), ("bot_message", False), (None, False)])
def test_joins_and_pins_are_noise(subtype, noise):
    assert message_text.is_noise({"subtype": subtype}) is noise

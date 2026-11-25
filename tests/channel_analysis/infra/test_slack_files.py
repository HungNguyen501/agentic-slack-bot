from unittest.mock import patch

import pytest
from slack_sdk.errors import SlackApiError

from channel_analysis.core.errors import ChannelAccessError, ChannelAnalysisError, FileUnreadableError
from channel_analysis.core.models import FileMeta
from channel_analysis.infra.slack_files import SlackChannelFiles
from connectors.slack import FileDownloadError

TOKEN = "xoxb-test"
RAW = {
    "id": "F1", "name": "rows.csv", "filetype": "csv", "size": 12, "url_private_download": "https://files.slack.com/files-pri/T-F1/download/rows.csv",
    "channels": ["C1"], "groups": ["G1"], "shares": {"public": {"C2": []}, "private": {"G2": []}},
}  # fmt: skip


@pytest.fixture
def slack():
    with patch("channel_analysis.infra.slack_files.slack", autospec=True) as connector:
        connector.FileDownloadError = FileDownloadError
        connector.get_file_info.return_value = RAW
        connector.download_file.return_value = b"id\n1\n"
        yield connector


def test_describe_lists_every_channel_the_file_is_shared_in(slack):
    meta = SlackChannelFiles().describe("F1", TOKEN)

    assert meta == FileMeta("rows.csv", "csv", 12, frozenset({"C1", "G1", "C2", "G2"}))


def test_text_downloads_from_the_url_slack_gave_and_extracts_it(slack):
    assert SlackChannelFiles().text("F1", TOKEN) == "id\n1\n"

    slack.download_file.assert_called_once_with(RAW["url_private_download"], token=TOKEN, max_bytes=5 * 1024 * 1024, allow_html=False)


def test_the_last_file_is_not_downloaded_again_for_the_next_page(slack):
    files = SlackChannelFiles()

    files.text("F1", TOKEN)
    files.text("F1", TOKEN)

    slack.download_file.assert_called_once()


def test_a_failed_download_is_an_unreadable_file(slack):
    slack.download_file.side_effect = FileDownloadError("Slack answered 403 for the file.")

    with pytest.raises(FileUnreadableError, match="403"):
        SlackChannelFiles().text("F1", TOKEN)


def test_a_file_with_no_link_is_unreadable(slack):
    slack.get_file_info.return_value = {"id": "F1", "name": "x.csv", "filetype": "csv"}

    with pytest.raises(FileUnreadableError, match="no download link"):
        SlackChannelFiles().text("F1", TOKEN)


def test_a_missing_file_is_a_plain_error(slack):
    slack.get_file_info.side_effect = SlackApiError("x", {"error": "file_not_found"})

    with pytest.raises(ChannelAnalysisError, match="can't find that file"):
        SlackChannelFiles().describe("F9", TOKEN)


def test_a_missing_scope_is_the_files_scope(slack):
    slack.get_file_info.side_effect = SlackApiError("x", {"error": "missing_scope", "needed": "files:read"})

    with pytest.raises(ChannelAccessError) as raised:
        SlackChannelFiles().describe("F1", TOKEN)

    assert raised.value.code == "files_scope"
    assert "files:read" in str(raised.value)


def test_describe_gives_the_same_kind_the_files_text_is_read_as(slack):
    slack.get_file_info.return_value = RAW | {"name": "rows.backup", "filetype": "csv"}

    assert SlackChannelFiles().describe("F1", TOKEN).filetype == "csv"

    slack.get_file_info.return_value = RAW | {"name": "data.csv", "filetype": "text"}

    assert SlackChannelFiles().describe("F1", TOKEN).filetype == "csv"


def test_an_html_file_is_downloaded_with_html_allowed_and_any_other_is_not(slack):
    slack.get_file_info.return_value = {**RAW, "name": "report.html", "filetype": "html"}
    slack.download_file.return_value = b"<p>1</p>"

    assert SlackChannelFiles().text("F1", TOKEN) == "1"

    assert slack.download_file.call_args.kwargs["allow_html"] is True

from unittest.mock import patch

from slack_sdk.errors import SlackApiError

from dataset_sync.infra.slack_notifier import SlackFailureNotifier


def test_a_failure_report_is_posted_to_the_channel_with_the_bots_token():
    with patch("dataset_sync.infra.slack_notifier.slack.post_message", autospec=True) as post:
        SlackFailureNotifier().post("C1", "it broke", "xoxb-test")

    post.assert_called_once_with("C1", "it broke", token="xoxb-test")


def test_a_failure_to_report_is_logged_not_raised():
    error = SlackApiError("x", {"error": "channel_not_found"})
    with patch("dataset_sync.infra.slack_notifier.slack.post_message", autospec=True, side_effect=error):
        SlackFailureNotifier().post("C1", "it broke", "xoxb-test")

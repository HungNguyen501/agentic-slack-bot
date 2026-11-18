import logging

from slack_sdk.errors import SlackApiError

from connectors import slack

log = logging.getLogger("dataset_sync.slack_notifier")


class SlackFailureNotifier:
    def post(self, channel: str, text: str, bot_token: str) -> None:
        try:
            slack.post_message(channel, text, token=bot_token)
        except SlackApiError:
            log.exception("Could not report a failed dataset sync in channel %s", channel)

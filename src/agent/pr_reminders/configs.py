import re

from common.configs import AgentConfigs


class PrRemindersConfigs:
    """Every constant of PR review reminders: the patterns it reads PR links and mentions with, the GraphQL fields, Slack pacing and
    retry limits, and the wording of a channel it can't read."""

    # A user mention as Slack writes it (<@U123ABCDE> or <@U123ABCDE|name>) or a bare user ID. Channel, here and user-group
    # mentions don't match, so a request can't be used to broadcast. Real IDs are at least 9 characters; requiring that keeps
    # a word like UNKNOWN or WHO, which an LLM might pass, from becoming a mention that pings nobody.
    USER_MENTION_RE = re.compile(r"<@([UW][A-Z0-9]{8,})(?:\|[^>]*)?>|([UW][A-Z0-9]{8,})")

    SCHEDULE_ID = frozenset({"schedule_id"})

    PR_URL_RE = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)")

    # GraphQL's Int is 32-bit: one larger number makes GitHub reject the whole query, failing every PR in the batch.
    MAX_PR_NUMBER = 2**31 - 1
    MAX_PR_NUMBER_DIGITS = len(str(MAX_PR_NUMBER))

    PR_FIELDS = "url title state isDraft reviewDecision createdAt latestOpinionatedReviews(first: 20) { totalCount nodes { state } }"

    # Slack errors meaning "this bot can't read that channel" (retrying won't fix them), and what to tell the user.
    CHANNEL_READ_HELP = {
        "not_in_channel": "I'm not a member of that channel, so I can't read it. Invite me and try again.",
        "channel_not_found": "I can't find that channel.",
        "missing_scope": "My Slack app is missing the channels:history / groups:history scopes. An admin needs to add them and reinstall the app.",
        "is_archived": "That channel is archived.",
    }

    # Slack allows about one message per second per channel, and a run can post into many threads of one channel.
    POST_GAP_SECONDS = 1.0

    # A 429's Retry-After can be long; waiting it all out would hold the worker and run into the job timeout.
    MAX_RETRY_AFTER_SECONDS = 30.0

    # A thread reminded less than this long ago is skipped. The scheduler can fire a freshly created reminder seconds after
    # its first check, while a normal tick is a whole interval after the last one; half the interval allows for jitter.
    RECENTLY_REMINDED_SECONDS = AgentConfigs.PR_REMINDER_MIN_INTERVAL_MINUTES * 60 / 2

    # reactions.add errors that no other message will escape: the app can't react at all.
    STOP_REACTING_CODES = frozenset({"missing_scope", "invalid_auth", "not_authed", "account_inactive", "token_revoked"})

    CHANNEL_ID_RE = re.compile(r"[CG][A-Z0-9]{6,}")

    # Each linked thread costs one chat.getPermalink call; past this the summary just counts the rest.
    MAX_PERMALINKS = 5

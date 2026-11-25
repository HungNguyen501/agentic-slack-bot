from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, create_autospec, patch

import pytest
from slack_sdk.errors import SlackApiError, SlackRequestError

from agent.pr_reminders.configs import PrRemindersConfigs
from agent.pr_reminders import service
from agent.pr_reminders.pr_links import PrLink, PrRef
from agent.pr_reminders.queries import GithubQueryError
from agent.pr_reminders.status import Bucket, PrStatus
from characters.core.models import Character
from characters.core.service import CharacterService
from common.configs import AgentConfigs
from schedules.core.models import Schedule
PR1, PR2, PR3 = (PrRef("org", "repo", n) for n in (1, 2, 3))
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
IN_HOURS = datetime(2026, 11, 4, 5, 0, tzinfo=UTC)  # 12:00 in Vietnam
NIGHT = datetime(2026, 11, 4, 18, 0, tzinfo=UTC)  # 01:00 in Vietnam
MENTIONS = ["U1", "U2"]
CONFIG = {"scope": "channel", "origin_channel": "C9", "origin_thread_ts": "9.0", "lookback_hours": 72, "mentions": MENTIONS}


def link(ref, thread_ts, message_ts=None, channel="C1", reactable=False, reacted=False, message_refs=None):
    """A link; a reactable one is, unless `message_refs` says otherwise, the only PR in its message."""
    return PrLink(ref, channel, thread_ts, message_ts or thread_ts, reactable=reactable, reacted=reacted, message_refs=tuple(message_refs or [ref]))


def graphql_payload(*nodes_by_number):
    return {"data": {"r0": {f"p{number}": node for number, node in nodes_by_number}}}


@pytest.fixture(autouse=True)
def no_sleep():
    with patch("agent.pr_reminders.service.time.sleep", autospec=True) as sleep:
        yield sleep


@pytest.fixture
def slack():
    with patch("agent.pr_reminders.service.slack", autospec=True) as slack:
        slack.split_message.side_effect = lambda text: [text]
        yield slack


@pytest.fixture
def github():
    with patch("agent.pr_reminders.service.github", autospec=True) as github:
        yield github


def slack_error(code=None, status=200, retry_after=None):
    response = MagicMock(status_code=status, headers={"Retry-After": retry_after} if retry_after else {})
    response.get.return_value = code
    return SlackApiError("boom", response)


# --- collect_links ---------------------------------------------------------------------------------


def test_collect_links_thread_scope_reads_the_thread_and_skips_own_messages(bot, slack):
    slack.conversations_replies.return_value = [
        {"ts": "1.0", "text": "https://github.com/org/repo/pull/1"},
        {"ts": "2.0", "app_id": "A_BOT", "text": "https://github.com/org/repo/pull/2"},
    ]

    scan = service.collect_links(bot, scope="thread", channel="C1", thread_ts="1.0", lookback_hours=None)

    slack.conversations_replies.assert_called_once_with("C1", "1.0", token="xoxb-test")
    assert [link.ref for link in scan.links] == [PR1]
    assert scan.truncated is False


def test_collect_links_channel_scope_reads_history_since_the_lookback(bot, slack):
    slack.conversations_history.return_value = ([], False)

    with patch("agent.pr_reminders.service.time.time", return_value=1_000_000.0):
        service.collect_links(bot, scope="channel", channel="C1", thread_ts="1.0", lookback_hours=2)

    slack.conversations_history.assert_called_once_with("C1", 1_000_000.0 - 2 * 3600, token="xoxb-test", max_messages=1000)


def test_collect_links_with_only_typed_reads_nothing_from_slack_and_returns_just_the_typed_prs(bot, slack):
    scan = service.collect_links(
        bot, scope="thread", channel="C1", thread_ts="9.0", lookback_hours=None, typed_urls=["https://github.com/org/repo/pull/121"], only_typed=True
    )

    slack.conversations_replies.assert_not_called()
    slack.conversations_history.assert_not_called()
    assert [(link.ref.number, link.channel, link.thread_ts) for link in scan.links] == [(121, "C1", "9.0")]
    assert scan.truncated is False


def test_collect_links_does_not_pick_up_the_other_prs_of_the_thread_when_only_typed_is_set(bot, slack):
    slack.conversations_replies.return_value = [{"ts": "1.0", "text": "https://github.com/org/other/pull/9 an older PR"}]

    scan = service.collect_links(
        bot, scope="thread", channel="C1", thread_ts="1.0", lookback_hours=None, typed_urls=["https://github.com/org/repo/pull/121"], only_typed=True
    )

    assert [link.ref.number for link in scan.links] == [121]


def test_collect_links_without_only_typed_still_adds_the_typed_prs_to_the_thread_scan(bot, slack):
    slack.conversations_replies.return_value = [{"ts": "1.0", "text": "https://github.com/org/other/pull/9"}]

    scan = service.collect_links(bot, scope="thread", channel="C1", thread_ts="1.0", lookback_hours=None, typed_urls=["https://github.com/org/repo/pull/121"])

    assert sorted(link.ref.number for link in scan.links) == [9, 121]


def test_collect_links_with_only_typed_and_nothing_typed_finds_nothing(bot, slack):
    scan = service.collect_links(bot, scope="thread", channel="C1", thread_ts="1.0", lookback_hours=None, only_typed=True)

    assert scan.links == []
    slack.conversations_replies.assert_not_called()


def test_collect_links_puts_typed_urls_in_the_request_thread(bot, slack):
    slack.conversations_history.return_value = ([{"ts": "5.0", "text": "https://github.com/org/repo/pull/1"}], False)

    scan = service.collect_links(
        bot, scope="channel", channel="C1", thread_ts="9.0", lookback_hours=2, typed_urls=["https://github.com/org/repo/pull/3"]
    )

    assert {link.ref: link.thread_ts for link in scan.links} == {PR1: "5.0", PR3: "9.0"}


def test_collect_links_says_when_the_history_scan_stopped_before_the_whole_window(bot, slack):
    slack.conversations_history.return_value = ([{"ts": "5.0", "text": "https://github.com/org/repo/pull/1"}], True)

    scan = service.collect_links(bot, scope="channel", channel="C1", thread_ts="9.0", lookback_hours=2)

    assert scan.truncated is True
    assert [link.ref for link in scan.links] == [PR1]


@pytest.mark.parametrize("code", sorted(PrRemindersConfigs.CHANNEL_READ_HELP))
def test_collect_links_turns_an_unreadable_channel_into_channel_read_error(bot, slack, code):
    slack.conversations_history.side_effect = slack_error(code)

    with pytest.raises(service.ChannelReadError) as exc:
        service.collect_links(bot, scope="channel", channel="C1", thread_ts="1.0", lookback_hours=2)

    assert exc.value.code == code


def test_collect_links_lets_other_slack_errors_propagate_for_a_retry(bot, slack):
    slack.conversations_history.side_effect = slack_error("ratelimited", status=429)

    with pytest.raises(SlackApiError):
        service.collect_links(bot, scope="channel", channel="C1", thread_ts="1.0", lookback_hours=2)


# --- check_and_remind ------------------------------------------------------------------------------


def test_check_and_remind_posts_one_message_per_thread_with_a_waiting_pr(bot, slack, github, pr_node):
    github.graphql.return_value = graphql_payload((1, pr_node()), (2, pr_node(title="Two")), (3, pr_node(decision="APPROVED")))
    links = [link(PR1, "1.0"), link(PR2, "1.0"), link(PR3, "5.0")]

    result = service.check_and_remind(bot, links, mentions=MENTIONS, now=NOW)

    slack.post_message.assert_called_once_with(
        "C1",
        "<@U1> <@U2> :eyes: Ủa alo, 2 cái PR này đang chờ review nè bà con ơi! :pepe-pray:\n"
        "• <https://github.com/org/repo/pull/1|org/repo#1 Fix it> — chờ 3 ngày :cricket: dế gáy rồi nha\n"
        "• <https://github.com/org/repo/pull/2|org/repo#2 Two> — chờ 3 ngày :cricket: dế gáy rồi nha",
        "1.0",
        token="xoxb-test",
    )
    assert result.posted == [("C1", "1.0")]
    assert result.waiting == 2


def test_check_and_remind_posts_in_each_waiting_threads_own_thread(bot, slack, github, pr_node):
    github.graphql.return_value = graphql_payload((1, pr_node()), (2, pr_node()))

    result = service.check_and_remind(bot, [link(PR1, "1.0"), link(PR2, "2.0")], mentions=MENTIONS, now=NOW)

    assert sorted(c.args[2] for c in slack.post_message.call_args_list) == ["1.0", "2.0"]
    assert sorted(result.posted) == [("C1", "1.0"), ("C1", "2.0")]


def test_check_and_remind_stays_silent_when_nothing_is_waiting(bot, slack, github, pr_node):
    github.graphql.return_value = graphql_payload((1, pr_node(decision="APPROVED")), (2, pr_node(state="MERGED")))

    result = service.check_and_remind(bot, [link(PR1, "1.0"), link(PR2, "2.0")], mentions=MENTIONS, now=NOW)

    slack.post_message.assert_not_called()
    assert result.waiting == 0


@pytest.mark.parametrize("include_author_only, posts", [(False, 0), (True, 1)])
def test_check_and_remind_posts_author_only_threads_only_when_asked(bot, slack, github, include_author_only, posts, pr_node):
    github.graphql.return_value = graphql_payload((1, pr_node(decision="CHANGES_REQUESTED")))

    service.check_and_remind(bot, [link(PR1, "1.0")], mentions=MENTIONS, include_author_only_threads=include_author_only, now=NOW)

    assert slack.post_message.call_count == posts


# --- marking approved PRs --------------------------------------------------------------------------


BOTH = (PR1, PR2)


def approved(pr_node):
    return pr_node(decision="APPROVED")


def test_check_and_remind_reacts_to_the_message_of_an_approved_pr(bot, slack, github, pr_node):
    github.graphql.return_value = graphql_payload((1, approved(pr_node)))

    result = service.check_and_remind(bot, [link(PR1, "1.0", message_ts="1.5", reactable=True)], mentions=MENTIONS, now=NOW)

    slack.add_reaction.assert_called_once_with("C1", "1.5", "white_check_mark", token=bot.bot_token)
    assert result.marked == 1
    slack.post_message.assert_not_called()


def test_check_and_remind_skips_a_message_that_already_has_the_reaction(bot, slack, github, pr_node):
    github.graphql.return_value = graphql_payload((1, approved(pr_node)))

    result = service.check_and_remind(bot, [link(PR1, "1.0", reactable=True, reacted=True)], mentions=MENTIONS, now=NOW)

    slack.add_reaction.assert_not_called()
    assert result.marked == 0


def test_check_and_remind_does_not_react_to_a_link_typed_in_the_request(bot, slack, github, pr_node):
    github.graphql.return_value = graphql_payload((1, approved(pr_node)))

    service.check_and_remind(bot, [link(PR1, "1.0")], mentions=MENTIONS, now=NOW)

    slack.add_reaction.assert_not_called()


@pytest.mark.parametrize(
    "decision, state",
    [("REVIEW_REQUIRED", "OPEN"), ("CHANGES_REQUESTED", "OPEN")],
    ids=["waiting", "changes-requested"],
)
def test_check_and_remind_does_not_react_to_pull_requests_still_open_and_unapproved(bot, slack, github, pr_node, decision, state):
    github.graphql.return_value = graphql_payload((1, pr_node(decision=decision, state=state)))

    service.check_and_remind(bot, [link(PR1, "1.0", reactable=True)], mentions=MENTIONS, now=NOW)

    slack.add_reaction.assert_not_called()


def test_check_and_remind_does_not_react_to_a_draft(bot, slack, github, pr_node):
    github.graphql.return_value = graphql_payload((1, pr_node(is_draft=True)))

    service.check_and_remind(bot, [link(PR1, "1.0", reactable=True)], mentions=MENTIONS, now=NOW)

    slack.add_reaction.assert_not_called()


@pytest.mark.parametrize(
    "decision, state, emoji",
    [
        ("APPROVED", "OPEN", "white_check_mark"),
        ("APPROVED", "MERGED", "white_check_mark"),
        ("REVIEW_REQUIRED", "MERGED", "white_check_mark"),
        ("REVIEW_REQUIRED", "CLOSED", "x"),
    ],
    ids=["approved", "approved-and-merged", "merged-unreviewed", "closed"],
)
def test_check_and_remind_reacts_with_the_emoji_that_matches_how_the_pr_finished(bot, slack, github, pr_node, decision, state, emoji):
    github.graphql.return_value = graphql_payload((1, pr_node(decision=decision, state=state)))

    result = service.check_and_remind(bot, [link(PR1, "1.0", message_ts="1.5", reactable=True)], mentions=MENTIONS, now=NOW)

    slack.add_reaction.assert_called_once_with("C1", "1.5", emoji, token=bot.bot_token)
    assert result.marked == 1
    slack.post_message.assert_not_called()


def test_check_and_remind_marks_a_message_with_an_approved_and_a_merged_pr(bot, slack, github, pr_node):
    github.graphql.return_value = graphql_payload((1, approved(pr_node)), (2, pr_node(state="MERGED")))
    links = [link(PR1, "1.0", message_ts="1.5", reactable=True, message_refs=BOTH), link(PR2, "1.0", message_ts="1.5", reactable=True, message_refs=BOTH)]

    service.check_and_remind(bot, links, mentions=MENTIONS, now=NOW)

    slack.add_reaction.assert_called_once_with("C1", "1.5", "white_check_mark", token=bot.bot_token)


def test_check_and_remind_marks_a_message_whose_prs_were_all_closed_once(bot, slack, github, pr_node):
    github.graphql.return_value = graphql_payload((1, pr_node(state="CLOSED")), (2, pr_node(state="CLOSED")))
    links = [link(PR1, "1.0", message_ts="1.5", reactable=True, message_refs=BOTH), link(PR2, "1.0", message_ts="1.5", reactable=True, message_refs=BOTH)]

    service.check_and_remind(bot, links, mentions=MENTIONS, now=NOW)

    slack.add_reaction.assert_called_once_with("C1", "1.5", "x", token=bot.bot_token)


def test_check_and_remind_leaves_a_message_alone_when_its_prs_were_closed_and_merged(bot, slack, github, pr_node):
    github.graphql.return_value = graphql_payload((1, pr_node(state="CLOSED")), (2, pr_node(state="MERGED")))
    links = [link(PR1, "1.0", message_ts="1.5", reactable=True, message_refs=BOTH), link(PR2, "1.0", message_ts="1.5", reactable=True, message_refs=BOTH)]

    result = service.check_and_remind(bot, links, mentions=MENTIONS, now=NOW)

    slack.add_reaction.assert_not_called()
    assert result.marked == 0


def test_check_and_remind_does_not_mark_a_message_that_also_links_a_pr_still_waiting(bot, slack, github, pr_node):
    github.graphql.return_value = graphql_payload((1, approved(pr_node)), (2, pr_node()))

    links = [link(PR1, "1.0", message_ts="1.5", reactable=True, message_refs=BOTH), link(PR2, "1.0", message_ts="1.5", reactable=True, message_refs=BOTH)]

    service.check_and_remind(bot, links, mentions=MENTIONS, now=NOW)

    slack.add_reaction.assert_not_called()


def test_check_and_remind_marks_a_message_once_when_all_its_prs_are_approved(bot, slack, github, pr_node):
    github.graphql.return_value = graphql_payload((1, approved(pr_node)), (2, approved(pr_node)))

    links = [link(PR1, "1.0", message_ts="1.5", reactable=True, message_refs=BOTH), link(PR2, "1.0", message_ts="1.5", reactable=True, message_refs=BOTH)]

    result = service.check_and_remind(bot, links, mentions=MENTIONS, now=NOW)

    slack.add_reaction.assert_called_once()
    assert result.marked == 1


def test_check_and_remind_does_not_mark_a_message_with_a_pr_left_unchecked_by_the_limit(bot, slack, github, pr_node):
    github.graphql.return_value = graphql_payload((1, approved(pr_node)))
    links = [link(PR1, "1.0", message_ts="1.5", reactable=True, message_refs=BOTH), link(PR2, "1.0", message_ts="1.5", reactable=True, message_refs=BOTH)]

    with patch.object(AgentConfigs, "PR_REMINDER_MAX_PRS_CHECKED", 1):
        result = service.check_and_remind(bot, links, mentions=MENTIONS, now=NOW)

    slack.add_reaction.assert_not_called()
    assert (result.truncated, result.marked) == (1, 0)


def test_check_and_remind_does_not_mark_the_older_message_of_a_pr_that_a_newer_message_links_again(bot, slack, github, pr_node):
    # M1 links approved PR1 and waiting PR2; the newer M2 links PR2 again, so the scan keeps PR2 only under M2. M1 still
    # links PR2, which is waiting, so it must not be marked as done.
    github.graphql.return_value = graphql_payload((1, approved(pr_node)), (2, pr_node()))
    links = [link(PR1, "1.0", message_ts="1.0", reactable=True, message_refs=BOTH), link(PR2, "2.0", message_ts="2.0", reactable=True, message_refs=(PR2,))]

    result = service.check_and_remind(bot, links, mentions=MENTIONS, now=NOW)

    slack.add_reaction.assert_not_called()
    assert result.marked == 0


def test_check_and_remind_marks_both_messages_when_the_pr_they_share_is_approved_too(bot, slack, github, pr_node):
    github.graphql.return_value = graphql_payload((1, approved(pr_node)), (2, approved(pr_node)))
    links = [link(PR1, "1.0", message_ts="1.0", reactable=True, message_refs=BOTH), link(PR2, "2.0", message_ts="2.0", reactable=True, message_refs=(PR2,))]

    result = service.check_and_remind(bot, links, mentions=MENTIONS, now=NOW)

    assert [call.args[1] for call in slack.add_reaction.call_args_list] == ["2.0", "1.0"]
    assert result.marked == 2


def test_check_and_remind_does_not_count_a_reaction_somebody_added_first(bot, slack, github, pr_node):
    github.graphql.return_value = graphql_payload((1, approved(pr_node)), (2, approved(pr_node)))
    slack.add_reaction.side_effect = [slack_error("already_reacted"), None]

    links = [link(PR1, "1.0", message_ts="1.5", reactable=True), link(PR2, "2.0", message_ts="2.5", reactable=True)]

    result = service.check_and_remind(bot, links, mentions=MENTIONS, now=NOW)

    assert slack.add_reaction.call_count == 2
    assert result.marked == 1


def test_check_and_remind_still_reminds_when_a_reaction_fails(bot, slack, github, pr_node):
    github.graphql.return_value = graphql_payload((1, approved(pr_node)), (2, pr_node()))
    slack.add_reaction.side_effect = slack_error("message_not_found")

    result = service.check_and_remind(bot, [link(PR1, "1.0", reactable=True), link(PR2, "2.0", reactable=True)], mentions=MENTIONS, now=NOW)

    assert result.posted == [("C1", "2.0")]
    assert result.marked == 0


def test_check_and_remind_tries_the_next_message_after_a_failed_reaction(bot, slack, github, pr_node):
    github.graphql.return_value = graphql_payload((1, approved(pr_node)), (2, approved(pr_node)))
    slack.add_reaction.side_effect = [slack_error("message_not_found"), None]

    result = service.check_and_remind(bot, [link(PR1, "1.0", reactable=True), link(PR2, "2.0", reactable=True)], mentions=MENTIONS, now=NOW)

    assert slack.add_reaction.call_count == 2
    assert result.marked == 1


@pytest.mark.parametrize(
    "error",
    [slack_error("missing_scope"), slack_error(status=429, retry_after="5"), OSError("connection reset")],
    ids=["no-scope", "rate-limited", "dropped"],
)
def test_check_and_remind_stops_reacting_when_every_message_would_fail_the_same_way(bot, slack, github, pr_node, error):
    github.graphql.return_value = graphql_payload((1, approved(pr_node)), (2, approved(pr_node)))
    slack.add_reaction.side_effect = error

    result = service.check_and_remind(bot, [link(PR1, "1.0", reactable=True), link(PR2, "2.0", reactable=True)], mentions=MENTIONS, now=NOW)

    slack.add_reaction.assert_called_once()
    assert result.marked == 0


def test_check_and_remind_reacts_to_at_most_a_capped_number_of_messages_per_run(bot, slack, github, pr_node):
    count = AgentConfigs.PR_REMINDER_MAX_REACTIONS_PER_RUN + 5
    github.graphql.return_value = graphql_payload(*[(n, approved(pr_node)) for n in range(1, count + 1)])
    links = [link(PrRef("org", "repo", n), f"{n}.0", reactable=True) for n in range(1, count + 1)]

    result = service.check_and_remind(bot, links, mentions=MENTIONS, now=NOW)

    assert slack.add_reaction.call_count == AgentConfigs.PR_REMINDER_MAX_REACTIONS_PER_RUN
    assert result.marked == AgentConfigs.PR_REMINDER_MAX_REACTIONS_PER_RUN


def test_collect_links_marks_links_whose_message_already_has_the_reaction(bot, slack):
    slack.conversations_replies.return_value = [
        {"ts": "1.0", "text": "https://github.com/org/repo/pull/1", "reactions": [{"name": "white_check_mark", "count": 1, "users": ["U9"]}]},
        {"ts": "2.0", "text": "https://github.com/org/repo/pull/2", "reactions": [{"name": "eyes", "count": 1, "users": ["U9"]}]},
        {"ts": "3.0", "text": "https://github.com/org/repo/pull/3", "reactions": [{"name": "x", "count": 1, "users": ["U9"]}]},
    ]

    scan = service.collect_links(bot, scope="thread", channel="C1", thread_ts="1.0", lookback_hours=None)

    assert {found.ref.number: (found.reactable, found.reacted) for found in scan.links} == {1: (True, True), 2: (True, False), 3: (True, True)}


def test_check_result_summarizes_its_buckets_in_bucket_order():
    statuses = [PrStatus(link(PR1, "1.0"), bucket, "t", None) for bucket in (Bucket.MERGED, Bucket.WAITING, Bucket.MERGED)]

    assert service.CheckResult(statuses).bucket_summary() == "waiting=1, merged=2"
    assert service.CheckResult([]).bucket_summary() == ""


def test_check_and_remind_does_not_call_github_without_links(bot, slack, github):
    result = service.check_and_remind(bot, [], mentions=MENTIONS, now=NOW)

    github.graphql.assert_not_called()
    assert result.statuses == []


def test_check_and_remind_checks_only_the_newest_prs_up_to_the_limit(bot, slack):
    links = [link(PrRef("org", "repo", n), f"{n}.0") for n in range(1, 31)]

    with (
        patch.object(AgentConfigs, "PR_REMINDER_MAX_PRS_CHECKED", 25),
        patch("agent.pr_reminders.service.fetch_statuses", autospec=True, return_value=[]) as fetch,
    ):
        result = service.check_and_remind(bot, links, mentions=MENTIONS, now=NOW)

    [checked] = fetch.call_args.args
    assert len(checked) == 25
    assert result.truncated == 5
    assert checked[0].ref.number == 30


def test_check_and_remind_checks_up_to_a_hundred_prs_by_default(bot, slack):
    links = [link(PrRef("org", "repo", n), f"{n}.0") for n in range(1, 106)]

    with patch("agent.pr_reminders.service.fetch_statuses", autospec=True, return_value=[]) as fetch:
        result = service.check_and_remind(bot, links, mentions=MENTIONS, now=NOW)

    assert len(fetch.call_args.args[0]) == 100
    assert result.truncated == 5


def test_check_and_remind_posts_to_the_most_overdue_threads_first_and_counts_the_rest(bot, slack, github, pr_node):
    github.graphql.return_value = graphql_payload(
        (1, pr_node(created_at="2026-11-03T10:00:00Z")),  # newest
        (2, pr_node(created_at="2026-10-20T10:00:00Z")),  # oldest
        (3, pr_node(created_at="2026-10-30T10:00:00Z")),
    )

    with patch.object(AgentConfigs, "PR_REMINDER_MAX_THREADS_POSTED", 2):
        result = service.check_and_remind(bot, [link(PR1, "1.0"), link(PR2, "2.0"), link(PR3, "3.0")], mentions=MENTIONS, now=NOW)

    assert result.posted == [("C1", "2.0"), ("C1", "3.0")]
    assert result.deferred_threads == 1


def test_check_and_remind_ranks_a_thread_by_its_oldest_waiting_pr(bot, slack, github, pr_node):
    # Thread 1.0 holds a very old PR and a brand-new one; thread 2.0 holds one mid-age PR. The old PR makes 1.0 the most overdue.
    github.graphql.return_value = graphql_payload(
        (1, pr_node(created_at="2026-10-01T10:00:00Z")),
        (2, pr_node(created_at="2026-11-03T10:00:00Z")),
        (3, pr_node(created_at="2026-10-20T10:00:00Z")),
    )

    with patch.object(AgentConfigs, "PR_REMINDER_MAX_THREADS_POSTED", 1):
        result = service.check_and_remind(bot, [link(PR1, "1.0"), link(PR2, "1.0"), link(PR3, "2.0")], mentions=MENTIONS, now=NOW)

    assert result.posted == [("C1", "1.0")]


def test_check_and_remind_reminds_the_thread_reminded_longest_ago_first(bot, slack, github, pr_node):
    github.graphql.return_value = graphql_payload((1, pr_node()), (2, pr_node()), (3, pr_node()))
    last_reminded = {"C1:1.0": 200.0, "C1:2.0": 100.0}  # thread 3.0 was never reminded

    with patch.object(AgentConfigs, "PR_REMINDER_MAX_THREADS_POSTED", 2):
        result = service.check_and_remind(
            bot, [link(PR1, "1.0"), link(PR2, "2.0"), link(PR3, "3.0")], mentions=MENTIONS, last_reminded=last_reminded, now=NOW
        )

    assert result.posted == [("C1", "3.0"), ("C1", "2.0")]
    assert result.deferred_threads == 1


def test_a_recurring_reminder_takes_turns_when_more_threads_wait_than_fit_in_one_run(bot, db, collect, slack, github, pr_node, merge_config):
    get, _ = db
    collect.return_value = service.ScanResult([link(PR1, "1.0"), link(PR2, "2.0"), link(PR3, "3.0")], False)
    github.graphql.return_value = graphql_payload(
        (1, pr_node(created_at="2026-10-01T10:00:00Z")),
        (2, pr_node(created_at="2026-10-02T10:00:00Z")),
        (3, pr_node(created_at="2026-10-03T10:00:00Z")),
    )
    get.return_value = schedule()

    with patch.object(AgentConfigs, "PR_REMINDER_MAX_THREADS_POSTED", 2):
        service.run_reminder(bot, "s1", IN_HOURS)
        first_tick_threads = [call.args[2] for call in slack.post_message.call_args_list]
        saved = merge_config.call_args.args[1]["last_reminded"]

        slack.post_message.reset_mock()
        get.return_value = schedule(config=CONFIG | {"last_reminded": saved})
        service.run_reminder(bot, "s1", IN_HOURS + timedelta(minutes=30))
        second_tick_threads = [call.args[2] for call in slack.post_message.call_args_list]

    assert first_tick_threads == ["1.0", "2.0"]  # the two most overdue go first
    assert second_tick_threads[0] == "3.0"  # the thread that was left out goes first next time
    assert sorted(second_tick_threads) == ["1.0", "3.0"]


@pytest.mark.parametrize(
    "seconds_ago, posts",
    [(10, 0), (5 * 60, 0), (14 * 60 + 59, 0), (15 * 60, 1), (30 * 60, 1), (6 * 3600, 1)],
    ids=["10s", "5m", "just-under-half-the-interval", "exactly-half-the-interval", "a-full-interval", "hours"],
)
def test_check_and_remind_skips_a_thread_that_was_reminded_less_than_half_the_minimum_interval_ago(bot, slack, github, pr_node, seconds_ago, posts):
    github.graphql.return_value = graphql_payload((1, pr_node()))
    last_reminded = {"C1:1.0": NOW.timestamp() - seconds_ago}

    result = service.check_and_remind(bot, [link(PR1, "1.0")], mentions=MENTIONS, last_reminded=last_reminded, now=NOW)

    assert slack.post_message.call_count == posts
    assert result.waiting == 1  # skipped threads still count as waiting: the reminder must not end because of this


def test_check_and_remind_does_not_count_a_recently_reminded_thread_as_deferred(bot, slack, github, pr_node):
    github.graphql.return_value = graphql_payload((1, pr_node()))

    result = service.check_and_remind(
        bot, [link(PR1, "1.0")], mentions=MENTIONS, last_reminded={"C1:1.0": NOW.timestamp() - 60}, now=NOW
    )

    assert result.deferred_threads == 0
    assert result.posted == []


def test_a_tick_right_after_the_first_check_does_not_repeat_the_reminder_and_does_not_end_the_reminder(bot, db, collect, slack, github, pr_node, merge_config):
    # The scheduler can fire a brand-new reminder seconds after the first check posted and saved its time.
    get, set_status = db
    get.return_value = schedule(config=CONFIG | {"last_reminded": {"C1:1.0": IN_HOURS.timestamp() - 20}})
    github.graphql.return_value = graphql_payload((1, pr_node()))

    service.run_reminder(bot, "s1", IN_HOURS)

    slack.post_message.assert_not_called()
    set_status.assert_not_called()


def test_check_and_remind_puts_threads_waiting_for_review_before_author_only_threads(bot, slack, github, pr_node):
    github.graphql.return_value = graphql_payload(
        (1, pr_node(decision="CHANGES_REQUESTED", created_at="2026-10-01T10:00:00Z")),  # much older, but only waits on the author
        (2, pr_node(created_at="2026-11-03T10:00:00Z")),
    )

    with patch.object(AgentConfigs, "PR_REMINDER_MAX_THREADS_POSTED", 1):
        result = service.check_and_remind(bot, [link(PR1, "1.0"), link(PR2, "2.0")], mentions=MENTIONS, include_author_only_threads=True, now=NOW)

    assert result.posted == [("C1", "2.0")]
    assert result.deferred_threads == 1


def test_check_and_remind_keeps_going_when_one_thread_cannot_be_posted_to(bot, slack, github, pr_node):
    github.graphql.return_value = graphql_payload((1, pr_node()), (2, pr_node()))
    slack.post_message.side_effect = [slack_error("is_archived"), "ts"]

    result = service.check_and_remind(bot, [link(PR1, "1.0"), link(PR2, "2.0")], mentions=MENTIONS, now=NOW)

    assert len(result.posted) == 1
    assert result.failed_posts == 1
    assert slack.post_message.call_count == 2


@pytest.mark.parametrize("error", [TimeoutError("timed out"), ConnectionResetError("reset"), SlackRequestError("bad request")], ids=["timeout", "reset", "request-error"])
def test_check_and_remind_treats_a_transport_error_as_a_failed_post_not_a_failed_job(bot, slack, github, pr_node, error):
    github.graphql.return_value = graphql_payload((1, pr_node()), (2, pr_node()))
    slack.post_message.side_effect = [error, "ts"]

    result = service.check_and_remind(bot, [link(PR1, "1.0"), link(PR2, "2.0")], mentions=MENTIONS, now=NOW)

    assert len(result.posted) == 1
    assert result.failed_posts == 1


def test_check_and_remind_backs_off_once_on_a_rate_limit_then_posts(bot, slack, github, no_sleep, pr_node):
    github.graphql.return_value = graphql_payload((1, pr_node()))
    slack.post_message.side_effect = [slack_error(status=429, retry_after="3"), "ts"]

    result = service.check_and_remind(bot, [link(PR1, "1.0")], mentions=MENTIONS, now=NOW)

    no_sleep.assert_called_once_with(3.0)
    assert result.posted == [("C1", "1.0")]


def test_check_and_remind_never_waits_longer_than_the_cap_for_a_rate_limit(bot, slack, github, no_sleep, pr_node):
    github.graphql.return_value = graphql_payload((1, pr_node()))
    slack.post_message.side_effect = [slack_error(status=429, retry_after="3600"), "ts"]

    service.check_and_remind(bot, [link(PR1, "1.0")], mentions=MENTIONS, now=NOW)

    no_sleep.assert_called_once_with(30.0)


def test_check_and_remind_spaces_posts_out(bot, slack, github, no_sleep, pr_node):
    github.graphql.return_value = graphql_payload((1, pr_node()), (2, pr_node()), (3, pr_node()))

    service.check_and_remind(bot, [link(PR1, "1.0"), link(PR2, "2.0"), link(PR3, "3.0")], mentions=MENTIONS, now=NOW)

    assert no_sleep.call_count == 2


def test_check_and_remind_posts_nothing_when_github_cannot_be_queried(bot, slack, github):
    github.graphql.return_value = {"errors": [{"message": "nope"}]}

    with pytest.raises(GithubQueryError):
        service.check_and_remind(bot, [link(PR1, "1.0")], mentions=MENTIONS, now=NOW)

    slack.post_message.assert_not_called()


def test_check_and_remind_shows_the_schedule_id_in_the_message(bot, slack, github, pr_node):
    github.graphql.return_value = graphql_payload((1, pr_node()))

    service.check_and_remind(bot, [link(PR1, "1.0")], mentions=MENTIONS, schedule_id="3f2a", now=NOW)

    assert "Lịch nhắc `3f2a`" in slack.post_message.call_args.args[1]


# --- run_reminder ----------------------------------------------------------------------------------


def schedule(status="active", kind="pr_reminder", config=CONFIG, created_at=None):
    return Schedule(
        id="s1", cron="*/15 * * * *", channel="C1", question="label", bot_id="bot-a", kind=kind, status=status, config=config,
        created_at=created_at or str(IN_HOURS - timedelta(hours=1)),
    )


@pytest.fixture
def merge_config():
    with patch("agent.pr_reminders.service._schedules.merge_schedule_config", autospec=True) as merge:
        yield merge


@pytest.fixture
def db(merge_config):
    with (
        patch("agent.pr_reminders.service._schedules.get_schedule", autospec=True) as get,
        patch("agent.pr_reminders.service._schedules.set_schedule_status", autospec=True) as set_status,
    ):
        yield get, set_status


@pytest.fixture
def collect():
    with patch("agent.pr_reminders.service.collect_links", autospec=True, return_value=service.ScanResult([link(PR1, "1.0")], False)) as collect:
        yield collect


def test_run_reminder_does_not_look_at_the_clock_because_the_cron_decides_when_it_runs(bot, db, collect, slack, github, pr_node):
    get, set_status = db
    get.return_value = schedule()
    github.graphql.return_value = graphql_payload((1, pr_node()))

    service.run_reminder(bot, "s1", NIGHT)

    collect.assert_called_once()
    slack.post_message.assert_called_once()
    set_status.assert_not_called()


@pytest.mark.parametrize(
    "found",
    [None, schedule(status="completed"), schedule(status="expired"), schedule(kind="question")],
    ids=["missing", "completed", "expired", "not-a-reminder"],
)
def test_run_reminder_skips_rows_that_are_gone_or_not_active_reminders(bot, db, collect, found):
    get, set_status = db
    get.return_value = found

    service.run_reminder(bot, "s1", IN_HOURS)

    collect.assert_not_called()
    set_status.assert_not_called()


def test_run_reminder_ends_a_reminder_with_a_malformed_config(bot, db, collect):
    get, set_status = db
    get.return_value = schedule(config={"scope": "nonsense"})

    service.run_reminder(bot, "s1", IN_HOURS)

    set_status.assert_called_once_with("s1", "completed")
    collect.assert_not_called()


def test_run_reminder_expires_a_reminder_past_its_time_limit_and_says_so_in_the_origin_thread(bot, db, collect, slack):
    get, set_status = db
    get.return_value = schedule(config=CONFIG | {"expires_at": (IN_HOURS - timedelta(minutes=1)).timestamp()})

    service.run_reminder(bot, "s1", IN_HOURS)

    set_status.assert_called_once_with("s1", "expired")
    assert slack.post_message.call_args.args[:3] == ("C9", slack.post_message.call_args.args[1], "9.0")
    assert "đã hết hạn" in slack.post_message.call_args.args[1]
    collect.assert_not_called()


def test_run_reminder_keeps_going_before_its_time_limit(bot, db, collect, slack, github, pr_node):
    get, set_status = db
    get.return_value = schedule(config=CONFIG | {"expires_at": (IN_HOURS + timedelta(hours=1)).timestamp()})
    github.graphql.return_value = graphql_payload((1, pr_node()))

    service.run_reminder(bot, "s1", IN_HOURS)

    set_status.assert_not_called()
    assert slack.post_message.call_args.args[2] == "1.0"


def test_run_reminder_never_expires_without_a_time_limit_however_old_it_is(bot, db, collect, slack, github, pr_node):
    get, set_status = db
    get.return_value = schedule(created_at=str(IN_HOURS - timedelta(days=400)))
    github.graphql.return_value = graphql_payload((1, pr_node()))

    service.run_reminder(bot, "s1", IN_HOURS)

    set_status.assert_not_called()


def test_run_reminder_ends_when_the_bot_can_no_longer_read_the_channel(bot, db, collect, slack):
    get, set_status = db
    get.return_value = schedule()
    collect.side_effect = service.ChannelReadError("not_in_channel")

    service.run_reminder(bot, "s1", IN_HOURS)

    set_status.assert_called_once_with("s1", "completed")
    assert "not_in_channel" in slack.post_message.call_args.args[1]


def test_run_reminder_reminds_and_keeps_going_while_a_pr_is_waiting(bot, db, collect, slack, github, pr_node):
    get, set_status = db
    get.return_value = schedule()
    github.graphql.return_value = graphql_payload((1, pr_node()))

    service.run_reminder(bot, "s1", IN_HOURS)

    assert slack.post_message.call_args.args[2] == "1.0"
    assert slack.post_message.call_args.args[1].startswith("<@U1> <@U2> ")
    set_status.assert_not_called()


def test_run_reminder_uses_the_schedules_channel_and_the_origin_thread_for_scanning(bot, db, collect, slack, github, pr_node):
    get, _ = db
    get.return_value = schedule(config={"scope": "thread", "origin_channel": "C1", "origin_thread_ts": "7.0", "mentions": MENTIONS})
    github.graphql.return_value = graphql_payload((1, pr_node()))

    service.run_reminder(bot, "s1", IN_HOURS)

    collect.assert_called_once_with(bot, scope="thread", channel="C1", thread_ts="7.0", lookback_hours=None)


def test_run_reminder_keeps_running_when_every_pr_is_approved(bot, db, collect, slack, github, pr_node):
    get, set_status = db
    get.return_value = schedule()
    github.graphql.return_value = graphql_payload((1, pr_node(decision="APPROVED")))

    service.run_reminder(bot, "s1", IN_HOURS)

    set_status.assert_not_called()
    slack.post_message.assert_not_called()


def test_run_reminder_keeps_running_when_there_are_no_pr_links_at_all(bot, db, collect, slack):
    get, set_status = db
    get.return_value = schedule()
    collect.return_value = service.ScanResult([], False)

    service.run_reminder(bot, "s1", IN_HOURS)

    set_status.assert_not_called()
    slack.post_message.assert_not_called()


def test_run_reminder_keeps_running_after_an_incomplete_check(bot, db, collect, slack, github):
    get, set_status = db
    get.return_value = schedule()
    collect.return_value = service.ScanResult([link(PR1, "1.0")], True)
    github.graphql.return_value = {"data": {"r0": None}, "errors": [{"type": "NOT_FOUND"}]}

    service.run_reminder(bot, "s1", IN_HOURS)

    set_status.assert_not_called()


def test_run_reminder_forgets_threads_that_are_no_longer_waiting(bot, db, collect, slack, github, pr_node, merge_config):
    get, _ = db
    github.graphql.return_value = graphql_payload((1, pr_node()))
    get.return_value = schedule(config=CONFIG | {"last_reminded": {"C1:gone": 1.0}})

    service.run_reminder(bot, "s1", IN_HOURS)

    merge_config.assert_called_once_with("s1", {"last_reminded": {"C1:1.0": IN_HOURS.timestamp()}})


def test_run_reminder_writes_nothing_when_its_progress_did_not_change(bot, db, collect, slack, github, pr_node, merge_config):
    get, _ = db
    github.graphql.return_value = graphql_payload((1, pr_node()))
    get.return_value = schedule(config=CONFIG | {"last_reminded": {"C1:1.0": IN_HOURS.timestamp()}})

    service.run_reminder(bot, "s1", IN_HOURS)

    merge_config.assert_not_called()


def test_run_reminder_leaves_the_reminder_active_when_github_fails(bot, db, collect, slack, github):
    get, set_status = db
    get.return_value = schedule()
    github.graphql.side_effect = RuntimeError("github down")

    with pytest.raises(RuntimeError):
        service.run_reminder(bot, "s1", IN_HOURS)

    set_status.assert_not_called()
    slack.post_message.assert_not_called()


# --- characters ------------------------------------------------------------------------------------

ANNA = Character(
    name="anna",
    display_name="Anna",
    icon_emoji=":star:",
    bindings=("feature:pr_reminder",),
    voices={"pr_reminder": {"headers": ["{who} ANNA says {subject}"], "schedule_footer": "ANNA footer {schedule_id}", "expired": "ANNA expired {schedule_id}",
                            "unreadable": "ANNA unreadable {schedule_id} {error_code}"}},
)


@pytest.fixture
def characters():
    cast = create_autospec(CharacterService, instance=True)
    cast.character_for_feature.return_value = ANNA
    return cast


def test_check_and_remind_posts_each_thread_in_its_characters_voice_and_identity(bot, slack, github, pr_node, characters):
    github.graphql.return_value = graphql_payload((1, pr_node()))

    service.check_and_remind(bot, [link(PR1, "1.0")], mentions=MENTIONS, schedule_id="3f2a", now=NOW, characters=characters)

    args, kwargs = slack.post_message.call_args
    assert args[1].splitlines()[0] == "<@U1> <@U2> ANNA says cái PR này"
    assert args[1].splitlines()[-1] == "ANNA footer 3f2a"
    assert kwargs == {"token": "xoxb-test", "username": "Anna", "icon_emoji": ":star:"}
    characters.character_for_feature.assert_called_once_with("pr_reminder", "C1", "1.0", characters.usable_characters.return_value)


def test_check_and_remind_asks_for_a_character_once_per_thread(bot, slack, github, pr_node, characters):
    github.graphql.return_value = graphql_payload((1, pr_node()), (2, pr_node()))

    service.check_and_remind(bot, [link(PR1, "1.0"), link(PR2, "2.0")], mentions=MENTIONS, now=NOW, characters=characters)

    usable = characters.usable_characters.return_value
    assert sorted(call.args for call in characters.character_for_feature.call_args_list) == [("pr_reminder", "C1", "1.0", usable), ("pr_reminder", "C1", "2.0", usable)]


def test_check_and_remind_reads_the_characters_once_for_the_whole_run(bot, slack, github, pr_node, characters):
    github.graphql.return_value = graphql_payload((1, pr_node()), (2, pr_node()), (3, pr_node()))

    service.check_and_remind(bot, [link(PR1, "1.0"), link(PR2, "2.0"), link(PR3, "3.0")], mentions=MENTIONS, now=NOW, characters=characters)

    characters.usable_characters.assert_called_once_with()
    assert characters.character_for_feature.call_count == 3


def test_check_and_remind_does_not_read_the_characters_when_there_is_nothing_to_post(bot, slack, github, pr_node, characters):
    github.graphql.return_value = graphql_payload((1, pr_node(decision="APPROVED")))

    service.check_and_remind(bot, [link(PR1, "1.0")], mentions=MENTIONS, now=NOW, characters=characters)

    characters.usable_characters.assert_not_called()
    characters.character_for_feature.assert_not_called()


def test_check_and_remind_uses_the_default_voice_when_no_character_is_picked(bot, slack, github, pr_node, characters):
    characters.character_for_feature.return_value = None
    github.graphql.return_value = graphql_payload((1, pr_node()))

    service.check_and_remind(bot, [link(PR1, "1.0")], mentions=MENTIONS, now=NOW, characters=characters)

    args, kwargs = slack.post_message.call_args
    assert args[1].startswith("<@U1> <@U2> ") and "ANNA" not in args[1]
    assert kwargs == {"token": "xoxb-test"}


def test_run_reminder_posts_the_expiry_note_in_the_origin_threads_character(bot, db, collect, slack, characters):
    get, set_status = db
    get.return_value = schedule(config=CONFIG | {"expires_at": (IN_HOURS - timedelta(minutes=1)).timestamp()})

    service.run_reminder(bot, "s1", IN_HOURS, characters=characters)

    characters.character_for_feature.assert_called_once_with("pr_reminder", "C9", "9.0", None)
    assert slack.post_message.call_args.args[:3] == ("C9", "ANNA expired s1", "9.0")
    assert slack.post_message.call_args.kwargs["username"] == "Anna"
    set_status.assert_called_once_with("s1", "expired")


def test_run_reminder_posts_the_unreadable_note_in_the_origin_threads_character(bot, db, collect, slack, characters):
    get, _ = db
    get.return_value = schedule()
    collect.side_effect = service.ChannelReadError("not_in_channel")

    service.run_reminder(bot, "s1", IN_HOURS, characters=characters)

    assert slack.post_message.call_args.args[1] == "ANNA unreadable s1 not_in_channel"
    assert slack.post_message.call_args.kwargs["icon_emoji"] == ":star:"


def test_run_reminder_hands_the_characters_to_the_check(bot, db, collect, slack, github, pr_node, characters):
    get, _ = db
    get.return_value = schedule()
    github.graphql.return_value = graphql_payload((1, pr_node()))

    service.run_reminder(bot, "s1", IN_HOURS, characters=characters)

    assert slack.post_message.call_args.args[1].startswith("<@U1> <@U2> ANNA says")


def test_run_reminder_without_characters_posts_the_default_wording_with_no_identity(bot, db, collect, slack):
    get, set_status = db
    get.return_value = schedule(config=CONFIG | {"expires_at": (IN_HOURS - timedelta(minutes=1)).timestamp()})

    service.run_reminder(bot, "s1", IN_HOURS)

    assert "đã hết hạn" in slack.post_message.call_args.args[1]
    assert slack.post_message.call_args.kwargs == {"token": "xoxb-test"}

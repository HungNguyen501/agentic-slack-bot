from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from slack_sdk.errors import SlackApiError, SlackRequestError

from agent.pr_reminders import service
from agent.pr_reminders.pr_links import PrLink, PrRef
from agent.pr_reminders.queries import GithubQueryError
from common.configs import AgentConfigs
from connectors.db.schedules import Schedule
PR1, PR2, PR3 = (PrRef("org", "repo", n) for n in (1, 2, 3))
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
# 12:00 in Vietnam: inside the hours recurring reminders are allowed to post.
IN_HOURS = datetime(2026, 11, 4, 5, 0, tzinfo=UTC)
NIGHT = datetime(2026, 11, 4, 18, 0, tzinfo=UTC)  # 01:00 in Vietnam
MENTIONS = ["U1", "U2"]
CONFIG = {"scope": "channel", "origin_channel": "C9", "origin_thread_ts": "9.0", "lookback_hours": 72, "mentions": MENTIONS, "has_reminded": True}


def link(ref, thread_ts, message_ts=None, channel="C1"):
    return PrLink(ref, channel, thread_ts, message_ts or thread_ts)


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


@pytest.mark.parametrize("code", sorted(service.CHANNEL_READ_HELP))
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
        "<@U1> <@U2> :eyes: Waiting for review\n"
        "• <https://github.com/org/repo/pull/1|org/repo#1 Fix it> — open 3d\n"
        "• <https://github.com/org/repo/pull/2|org/repo#2 Two> — open 3d",
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


def test_check_and_remind_posts_at_any_hour_because_active_hours_only_gate_scheduled_ticks(bot, slack, github, pr_node):
    github.graphql.return_value = graphql_payload((1, pr_node()))

    with patch("agent.pr_reminders.service.is_within_active_hours", autospec=True, return_value=False) as within:
        result = service.check_and_remind(bot, [link(PR1, "1.0")], mentions=MENTIONS, now=NIGHT)

    assert result.posted == [("C1", "1.0")]
    within.assert_not_called()


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

    assert "Schedule `3f2a`" in slack.post_message.call_args.args[1]


# --- run_reminder ----------------------------------------------------------------------------------


def schedule(status="active", kind="pr_reminder", config=CONFIG, created_at=None):
    return Schedule(
        id="s1", cron="*/15 * * * *", channel="C1", question="label", bot_id="bot-a", kind=kind, status=status, config=config,
        created_at=created_at or str(IN_HOURS - timedelta(hours=1)),
    )


@pytest.fixture
def merge_config():
    with patch("agent.pr_reminders.service.merge_schedule_config", autospec=True) as merge:
        yield merge


@pytest.fixture
def db(merge_config):
    with patch("agent.pr_reminders.service.get_schedule", autospec=True) as get, patch("agent.pr_reminders.service.set_schedule_status", autospec=True) as set_status:
        yield get, set_status


@pytest.fixture
def collect():
    with patch("agent.pr_reminders.service.collect_links", autospec=True, return_value=service.ScanResult([link(PR1, "1.0")], False)) as collect:
        yield collect


@pytest.mark.parametrize("now", [NIGHT, datetime(2026, 11, 4, 15, 0, tzinfo=UTC)], ids=["01:00", "22:00-sharp"])
def test_run_reminder_does_nothing_outside_the_active_hours(bot, db, collect, slack, github, now):
    get, set_status = db

    service.run_reminder(bot, "s1", now)

    get.assert_not_called()
    collect.assert_not_called()
    github.graphql.assert_not_called()
    slack.post_message.assert_not_called()
    set_status.assert_not_called()


def test_run_reminder_does_not_end_a_reminder_just_because_it_is_night(bot, db, collect, slack):
    get, set_status = db
    get.return_value = schedule()

    service.run_reminder(bot, "s1", NIGHT)

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


def test_run_reminder_expires_an_old_reminder_and_says_so_in_the_origin_thread(bot, db, collect, slack):
    get, set_status = db
    get.return_value = schedule(created_at=str(IN_HOURS - timedelta(days=8)))

    service.run_reminder(bot, "s1", IN_HOURS)

    set_status.assert_called_once_with("s1", "expired")
    assert slack.post_message.call_args.args[:3] == ("C9", slack.post_message.call_args.args[1], "9.0")
    assert "7 days" in slack.post_message.call_args.args[1]
    collect.assert_not_called()


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
    assert slack.post_message.call_args.args[1].startswith("<@U1> <@U2> :eyes: Waiting for review")
    set_status.assert_not_called()


def test_run_reminder_uses_the_schedules_channel_and_the_origin_thread_for_scanning(bot, db, collect, slack, github, pr_node):
    get, _ = db
    get.return_value = schedule(config={"scope": "thread", "origin_channel": "C1", "origin_thread_ts": "7.0", "mentions": MENTIONS})
    github.graphql.return_value = graphql_payload((1, pr_node()))

    service.run_reminder(bot, "s1", IN_HOURS)

    collect.assert_called_once_with(bot, scope="thread", channel="C1", thread_ts="7.0", lookback_hours=None)


def test_run_reminder_stops_itself_when_nothing_is_waiting(bot, db, collect, slack, github, pr_node):
    get, set_status = db
    get.return_value = schedule()
    github.graphql.return_value = graphql_payload((1, pr_node(decision="APPROVED")))

    service.run_reminder(bot, "s1", IN_HOURS)

    set_status.assert_called_once_with("s1", "completed")
    [call] = slack.post_message.call_args_list
    assert call.args[0] == "C9"
    assert call.args[2] == "9.0"
    assert "No PRs are waiting for review" in call.args[1]


def test_run_reminder_stays_active_when_some_prs_were_left_unchecked(bot, db, collect, slack, github, pr_node):
    get, set_status = db
    get.return_value = schedule()
    collect.return_value = service.ScanResult([link(PrRef("org", "repo", n), f"{n}.0") for n in range(1, 4)], False)
    github.graphql.return_value = graphql_payload(*((n, pr_node(decision="APPROVED")) for n in (2, 3)))

    with patch.object(AgentConfigs, "PR_REMINDER_MAX_PRS_CHECKED", 2):
        service.run_reminder(bot, "s1", IN_HOURS)

    set_status.assert_not_called()
    slack.post_message.assert_not_called()


def test_run_reminder_stays_active_when_none_of_its_prs_can_be_read(bot, db, collect, slack, github):
    get, set_status = db
    get.return_value = schedule()
    github.graphql.return_value = {"data": {"r0": None}, "errors": [{"type": "NOT_FOUND"}]}

    service.run_reminder(bot, "s1", IN_HOURS)

    set_status.assert_not_called()
    slack.post_message.assert_not_called()


def test_run_reminder_stays_active_when_one_pr_is_unreadable_even_if_the_others_are_approved(bot, db, collect, slack, github, pr_node):
    get, set_status = db
    get.return_value = schedule()
    collect.return_value = service.ScanResult([link(PR1, "1.0"), link(PrRef("org", "other", 9), "2.0")], False)
    # Links are checked newest first, so the "other" repo (message 2.0) is r0 and org/repo (message 1.0) is r1.
    github.graphql.return_value = {"data": {"r0": None, "r1": {"p1": pr_node(decision="APPROVED")}}}

    service.run_reminder(bot, "s1", IN_HOURS)

    set_status.assert_not_called()
    slack.post_message.assert_not_called()


def test_run_reminder_stays_active_when_the_slack_scan_stopped_early(bot, db, collect, slack, github, pr_node):
    get, set_status = db
    get.return_value = schedule()
    collect.return_value = service.ScanResult([link(PR1, "1.0")], True)
    github.graphql.return_value = graphql_payload((1, pr_node(decision="APPROVED")))

    service.run_reminder(bot, "s1", IN_HOURS)

    set_status.assert_not_called()


def test_run_reminder_keeps_watching_while_it_has_never_had_a_pr_to_remind_about(bot, db, collect, slack, github, pr_node, merge_config):
    get, set_status = db
    get.return_value = schedule(config=CONFIG | {"has_reminded": False})
    github.graphql.return_value = graphql_payload((1, pr_node(decision="APPROVED")))

    service.run_reminder(bot, "s1", IN_HOURS)

    set_status.assert_not_called()
    slack.post_message.assert_not_called()
    merge_config.assert_not_called()


def test_run_reminder_keeps_watching_when_the_channel_has_no_pr_links_yet(bot, db, collect, slack, merge_config):
    get, set_status = db
    get.return_value = schedule(config=CONFIG | {"has_reminded": False})
    collect.return_value = service.ScanResult([], False)

    service.run_reminder(bot, "s1", IN_HOURS)

    set_status.assert_not_called()


def test_run_reminder_remembers_that_it_has_reminded_so_that_it_can_end_later(bot, db, collect, slack, github, pr_node, merge_config):
    get, set_status = db
    get.return_value = schedule(config=CONFIG | {"has_reminded": False})
    github.graphql.return_value = graphql_payload((1, pr_node()))

    service.run_reminder(bot, "s1", IN_HOURS)

    merge_config.assert_called_once_with("s1", {"has_reminded": True, "last_reminded": {"C1:1.0": IN_HOURS.timestamp()}})
    set_status.assert_not_called()


def test_run_reminder_ends_only_after_it_has_reminded_and_everything_is_approved(bot, db, collect, slack, github, pr_node):
    get, set_status = db
    get.return_value = schedule(config=CONFIG | {"has_reminded": True})
    github.graphql.return_value = graphql_payload((1, pr_node(decision="APPROVED")))

    service.run_reminder(bot, "s1", IN_HOURS)

    set_status.assert_called_once_with("s1", "completed")


def test_run_reminder_forgets_threads_that_are_no_longer_waiting(bot, db, collect, slack, github, pr_node, merge_config):
    get, _ = db
    github.graphql.return_value = graphql_payload((1, pr_node()))
    get.return_value = schedule(config=CONFIG | {"last_reminded": {"C1:gone": 1.0}})

    service.run_reminder(bot, "s1", IN_HOURS)

    merge_config.assert_called_once_with("s1", {"has_reminded": True, "last_reminded": {"C1:1.0": IN_HOURS.timestamp()}})


def test_run_reminder_writes_nothing_when_its_progress_did_not_change(bot, db, collect, slack, github, pr_node, merge_config):
    get, _ = db
    github.graphql.return_value = graphql_payload((1, pr_node()))
    get.return_value = schedule(config=CONFIG | {"last_reminded": {"C1:1.0": IN_HOURS.timestamp()}})

    service.run_reminder(bot, "s1", IN_HOURS)

    merge_config.assert_not_called()


def test_run_reminder_ends_when_there_are_no_prs_at_all(bot, db, collect, slack):
    get, set_status = db
    get.return_value = schedule()
    collect.return_value = service.ScanResult([], False)

    service.run_reminder(bot, "s1", IN_HOURS)

    set_status.assert_called_once_with("s1", "completed")


def test_run_reminder_leaves_the_reminder_active_when_github_fails(bot, db, collect, slack, github):
    get, set_status = db
    get.return_value = schedule()
    github.graphql.side_effect = RuntimeError("github down")

    with pytest.raises(RuntimeError):
        service.run_reminder(bot, "s1", IN_HOURS)

    set_status.assert_not_called()
    slack.post_message.assert_not_called()

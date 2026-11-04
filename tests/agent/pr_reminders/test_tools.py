from unittest.mock import MagicMock, patch

import pytest
from slack_sdk.errors import SlackApiError

from agent.pr_reminders import tools
from agent.pr_reminders.pr_links import PrLink, PrRef
from agent.pr_reminders.service import ChannelReadError, CheckResult, ScanResult
from agent.pr_reminders.status import Bucket, PrStatus
from common.configs import AgentConfigs

LINK = PrLink(PrRef("org", "repo", 1), "C1", "1.0", "1.0")


def result(*buckets, posted=(), truncated=0, failed_posts=0):
    statuses = [PrStatus(LINK, bucket, "title", None) for bucket in buckets]
    return CheckResult(statuses, truncated, list(posted), failed_posts)


@pytest.fixture
def collect():
    with patch("agent.pr_reminders.tools.collect_links", autospec=True, return_value=ScanResult([LINK], False)) as collect:
        yield collect


@pytest.fixture
def check():
    with patch("agent.pr_reminders.tools.check_and_remind", autospec=True) as check:
        check.return_value = result(Bucket.WAITING, posted=[("C1", "1.0")])
        yield check


@pytest.fixture
def slack():
    with patch("agent.pr_reminders.tools.slack", autospec=True) as slack:
        slack.get_permalink.return_value = "https://slack.test/p1"
        yield slack


@pytest.fixture
def save():
    with patch("agent.pr_reminders.tools.save_progress", autospec=True) as save:
        yield save


@pytest.fixture
def db(save):
    with (
        patch("agent.pr_reminders.tools.count_active_pr_reminders", autospec=True, return_value=0) as count,
        patch("agent.pr_reminders.tools.add_pr_reminder", autospec=True) as add,
        patch("agent.pr_reminders.tools.remove_schedule", autospec=True) as remove,
    ):
        yield count, add, remove


def run(name, args, bot, user_id="U1", channel="C1", thread_ts="1.0"):
    """Call a tool as a user who mentioned two people (<@U1> and a bare U2); tests override `mentions` to change that."""
    return tools.dispatch_pr_reminder_tool(name, {"mentions": ["<@U1>", "U2"]} | args, user_id, channel, thread_ts, bot)


# --- who to remind --------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["check_pr_approvals", "schedule_pr_reminder"])
@pytest.mark.parametrize("mentions", [[], None], ids=["empty", "missing"])
def test_the_agent_is_told_to_ask_for_people_when_nobody_is_mentioned(bot, collect, check, db, name, mentions):
    args = {"scope": "channel", "cron": "*/30 * * * *", "mentions": mentions}

    reply = run(name, args, bot)

    assert reply.startswith("Who should I remind?")
    collect.assert_not_called()
    check.assert_not_called()


@pytest.mark.parametrize("name", ["check_pr_approvals", "schedule_pr_reminder"])
@pytest.mark.parametrize("mention", ["<!channel>", "<!here>", "<!subteam^S1|@team>", "@alice", "alice"])
def test_a_mention_that_is_not_a_person_is_rejected_before_any_io(bot, collect, check, db, name, mention):
    args = {"scope": "channel", "cron": "*/30 * * * *", "mentions": ["<@U1>", mention]}

    reply = run(name, args, bot)

    assert "by @mention" in reply
    collect.assert_not_called()
    check.assert_not_called()


def test_check_pr_approvals_reminds_the_mentioned_people_as_distinct_user_ids(bot, collect, check, slack):
    run("check_pr_approvals", {"scope": "channel", "mentions": ["<@U1>", "U2", "<@U1|alice>"]}, bot)

    assert check.call_args.kwargs["mentions"] == ["U1", "U2"]


def test_schedule_pr_reminder_stores_and_posts_with_the_mentioned_people(bot, collect, check, db):
    _, add, _ = db

    run("schedule_pr_reminder", {"cron": "*/30 * * * *", "scope": "channel", "mentions": ["<@U5>", "<@U6>"]}, bot)

    assert check.call_args.kwargs["mentions"] == ["U5", "U6"]
    assert add.call_args.args[5]["mentions"] == ["U5", "U6"]


# --- check_pr_approvals ----------------------------------------------------------------------------


def test_check_pr_approvals_scans_the_request_channel_and_thread(bot, collect, check, slack):
    run("check_pr_approvals", {"scope": "thread", "pr_urls": ["https://github.com/org/repo/pull/3", 7]}, bot, channel="C5", thread_ts="8.0")

    collect.assert_called_once_with(
        bot, scope="thread", channel="C5", thread_ts="8.0", lookback_hours=72, typed_urls=["https://github.com/org/repo/pull/3"]
    )


def test_check_pr_approvals_also_reports_threads_that_only_wait_on_the_author(bot, collect, check, slack):
    run("check_pr_approvals", {"scope": "channel"}, bot)

    assert check.call_args.kwargs["include_author_only_threads"] is True


@pytest.mark.parametrize("given, used", [(None, 72), (48, 48), (100_000, 336)], ids=["default", "given", "capped"])
def test_check_pr_approvals_lookback(bot, collect, check, slack, given, used):
    args = {"scope": "channel"} | ({"lookback_hours": given} if given else {})

    run("check_pr_approvals", args, bot)

    assert collect.call_args.kwargs["lookback_hours"] == used


@pytest.mark.parametrize("args", [{}, {"scope": "everywhere"}, {"scope": 3}], ids=["missing", "unknown", "not-a-string"])
def test_check_pr_approvals_rejects_a_bad_scope(bot, collect, args):
    assert run("check_pr_approvals", args, bot) == "scope must be 'thread' or 'channel'."
    collect.assert_not_called()


@pytest.mark.parametrize("lookback", [-5, "3d", 2.5, True], ids=["negative", "string", "float", "bool"])
def test_check_pr_approvals_rejects_a_bad_lookback(bot, collect, lookback):
    assert "lookback_hours" in run("check_pr_approvals", {"scope": "channel", "lookback_hours": lookback}, bot)


@pytest.mark.parametrize("scope, where", [("thread", "in this thread"), ("channel", "in this channel's recent top-level messages")])
def test_check_pr_approvals_says_when_there_are_no_links(bot, collect, check, scope, where):
    collect.return_value = ScanResult([], False)

    reply = run("check_pr_approvals", {"scope": scope}, bot)

    assert reply == f"I didn't find any GitHub pull request links {where}."
    check.assert_not_called()


def test_check_pr_approvals_warns_when_no_links_were_found_in_a_truncated_scan(bot, collect, check):
    collect.return_value = ScanResult([], True)

    reply = run("check_pr_approvals", {"scope": "channel"}, bot)

    assert reply.endswith("The window has more messages than I scan, so older ones may be missing.")


def test_check_pr_approvals_summarizes_what_was_checked_and_where_it_posted(bot, collect, check, slack):
    check.return_value = result(Bucket.WAITING, Bucket.WAITING_ON_AUTHOR, Bucket.APPROVED, Bucket.INACCESSIBLE, posted=[("C1", "1.0")], truncated=2)

    reply = run("check_pr_approvals", {"scope": "channel"}, bot)

    assert "Checked 4 PR(s): 1 waiting for review, 1 waiting on the author, 1 approved, closed or draft." in reply
    assert "1 PR(s) could not be read from GitHub" in reply
    assert "2 older PR(s) were not checked (limit 100)." in reply
    assert "Posted reminders in 1 thread(s): <https://slack.test/p1|thread>" in reply


def test_check_pr_approvals_counts_every_finished_bucket_as_approved_closed_or_draft(bot, collect, check, slack):
    check.return_value = result(Bucket.APPROVED, Bucket.DONE, Bucket.DRAFT, Bucket.WAITING)

    assert "1 waiting for review, 0 waiting on the author, 3 approved, closed or draft." in run("check_pr_approvals", {"scope": "channel"}, bot)


def test_check_pr_approvals_fetches_only_a_few_permalinks_and_counts_the_rest(bot, collect, check, slack):
    posted = [("C1", f"{n}.0") for n in range(8)]
    check.return_value = result(Bucket.WAITING, posted=posted)

    reply = run("check_pr_approvals", {"scope": "channel"}, bot)

    assert slack.get_permalink.call_count == 5
    assert "Posted reminders in 8 thread(s)" in reply
    assert reply.endswith("and 3 more.")


def test_check_pr_approvals_says_nothing_is_waiting_when_everything_was_checked_and_is_done(bot, collect, check, slack):
    check.return_value = result(Bucket.APPROVED, Bucket.DONE)

    reply = run("check_pr_approvals", {"scope": "channel"}, bot)

    assert "0 waiting for review" in reply
    assert "Nothing is waiting for review :white_check_mark:" in reply


@pytest.mark.parametrize(
    "check_result, scan_truncated",
    [
        (result(Bucket.APPROVED, Bucket.INACCESSIBLE), False),
        (result(Bucket.INACCESSIBLE), False),
        (result(Bucket.APPROVED, truncated=3), False),
        (result(Bucket.APPROVED), True),
    ],
    ids=["one-unreadable", "all-unreadable", "unchecked-prs", "scan-truncated"],
)
def test_check_pr_approvals_does_not_claim_nothing_is_waiting_when_the_check_was_incomplete(bot, collect, check, slack, check_result, scan_truncated):
    check.return_value = check_result
    collect.return_value = ScanResult([LINK], scan_truncated)

    reply = run("check_pr_approvals", {"scope": "channel"}, bot)

    assert "I can't say nothing is waiting, because the check was incomplete." in reply
    assert "Nothing is waiting for review :white_check_mark:" not in reply


def test_check_pr_approvals_says_when_the_history_scan_stopped_early(bot, collect, check, slack):
    check.return_value = result(Bucket.WAITING, posted=[("C1", "1.0")])
    collect.return_value = ScanResult([LINK], True)

    reply = run("check_pr_approvals", {"scope": "channel"}, bot)

    assert "more messages in that window than I scan (limit 1000), so older links may be missing." in reply


def test_check_pr_approvals_still_summarizes_when_a_permalink_cannot_be_fetched(bot, collect, check, slack):
    slack.get_permalink.side_effect = SlackApiError("nope", {"error": "message_not_found"})

    reply = run("check_pr_approvals", {"scope": "channel"}, bot)

    assert "Posted reminders in 1 thread(s)." in reply


def test_check_pr_approvals_says_when_threads_were_left_for_next_time(bot, collect, check, slack):
    check.return_value = result(Bucket.WAITING, posted=[("C1", "1.0")])
    check.return_value.deferred_threads = 4

    reply = run("check_pr_approvals", {"scope": "channel"}, bot)

    assert "4 more thread(s) were not reminded this time (limit 25 per run)." in reply


def test_check_pr_approvals_reports_threads_it_could_not_post_in(bot, collect, check, slack):
    check.return_value = result(Bucket.WAITING, failed_posts=2)

    assert "Could not post in 2 thread(s)." in run("check_pr_approvals", {"scope": "channel"}, bot)


# --- schedule_pr_reminder --------------------------------------------------------------------------


def test_schedule_pr_reminder_stores_the_reminder_under_the_id_shown_in_the_first_message(bot, collect, check, db):
    _, add, remove = db

    reply = run("schedule_pr_reminder", {"cron": "*/30 * * * *", "scope": "channel", "lookback_hours": 48}, bot, user_id="U7", channel="C1", thread_ts="4.0")

    schedule_id = check.call_args.kwargs["schedule_id"]
    add.assert_called_once_with(
        schedule_id,
        "*/30 * * * *",
        "C1",
        "PR review reminder · channel scope, last 48h",
        "bot-a",
        {
            "scope": "channel",
            "origin_channel": "C1",
            "origin_thread_ts": "4.0",
            "mentions": ["U1", "U2"],
            "lookback_hours": 48,
            "created_by": "U7",
            "has_reminded": False,
            "last_reminded": {},
        },
    )
    assert f"id: `{schedule_id}`" in reply
    assert "stops by itself" in reply
    assert "only sends reminders 07:00–22:00 Vietnam time (UTC+7)" in reply
    remove.assert_not_called()


def test_schedule_pr_reminder_stores_the_row_before_the_first_reminders_are_posted(bot, collect, check, db):
    _, add, _ = db
    calls = MagicMock()
    calls.attach_mock(add, "add")
    calls.attach_mock(check, "check")

    run("schedule_pr_reminder", {"cron": "*/30 * * * *", "scope": "channel"}, bot)

    assert [name for name, _, _ in calls.mock_calls] == ["add", "check"]


def test_schedule_pr_reminder_can_watch_another_channel_but_reports_back_in_the_request_thread(bot, collect, check, db):
    _, add, remove = db

    run("schedule_pr_reminder", {"cron": "*/30 * * * *", "scope": "channel", "channel": "C2OTHER99"}, bot, channel="C1", thread_ts="4.0")

    assert collect.call_args.kwargs["channel"] == "C2OTHER99"
    assert add.call_args.args[2] == "C2OTHER99"
    assert add.call_args.args[5]["origin_channel"] == "C1"


def test_schedule_pr_reminder_thread_scope_has_no_lookback(bot, collect, check, db):
    _, add, remove = db

    run("schedule_pr_reminder", {"cron": "*/30 * * * *", "scope": "thread"}, bot)

    assert add.call_args.args[5]["lookback_hours"] is None


def test_schedule_pr_reminder_keeps_the_schedule_when_nothing_is_waiting_because_a_pr_can_be_shared_later(bot, collect, check, db):
    _, add, remove = db
    check.return_value = result(Bucket.APPROVED)

    reply = run("schedule_pr_reminder", {"cron": "*/30 * * * *", "scope": "channel"}, bot)

    add.assert_called_once()
    remove.assert_not_called()
    assert f"id: `{add.call_args.args[0]}`" in reply
    assert "Nothing is waiting for review right now, so nothing was posted; I'll remind when a PR is." in reply


def test_schedule_pr_reminder_keeps_the_schedule_even_when_there_are_no_pr_links_yet(bot, collect, check, db):
    _, add, remove = db
    collect.return_value = ScanResult([], False)
    check.return_value = result()

    reply = run("schedule_pr_reminder", {"cron": "*/30 * * * *", "scope": "channel"}, bot)

    add.assert_called_once()
    remove.assert_not_called()
    assert "I'll remind when a PR is." in reply


def test_schedule_pr_reminder_saves_progress_after_the_first_check(bot, collect, check, db, save):
    _, add, _ = db
    check.return_value = result(Bucket.WAITING, posted=[("C1", "1.0")])

    run("schedule_pr_reminder", {"cron": "*/30 * * * *", "scope": "channel"}, bot)

    schedule_id, config, check_result, _ = save.call_args.args
    assert schedule_id == add.call_args.args[0]
    assert config.has_reminded is False
    assert check_result is check.return_value


def test_schedule_pr_reminder_removes_the_row_when_the_first_check_fails(bot, collect, check, db):
    _, add, remove = db
    check.side_effect = RuntimeError("github down")

    reply = run("schedule_pr_reminder", {"cron": "*/30 * * * *", "scope": "channel"}, bot)

    assert reply == "Error executing schedule_pr_reminder: github down"
    remove.assert_called_once_with(add.call_args.args[0], bot_id="bot-a")


@pytest.mark.parametrize(
    "cron, expected",
    [("* * * * *", "at most every 30 minutes"), ("nonsense", "exactly 5 fields"), ("", "exactly 5 fields"), ("99 * * * *", "not a valid cron expression")],
    ids=["too-often", "garbage", "empty", "out-of-range"],
)
def test_schedule_pr_reminder_rejects_a_bad_cron_before_touching_slack(bot, collect, check, db, cron, expected):
    reply = run("schedule_pr_reminder", {"cron": cron, "scope": "channel"}, bot)

    assert expected in reply
    collect.assert_not_called()


def test_schedule_pr_reminder_rejects_a_thread_scope_reminder_for_another_channel(bot, collect, check, db):
    reply = run("schedule_pr_reminder", {"cron": "*/30 * * * *", "scope": "thread", "channel": "C2OTHER99"}, bot, channel="C1")

    assert "can't target another channel" in reply
    collect.assert_not_called()


@pytest.mark.parametrize("channel", ["general", "#general", "c123456789", "C2"], ids=["name", "hash-name", "lowercase", "too-short"])
def test_schedule_pr_reminder_rejects_something_that_is_not_a_channel_id(bot, collect, check, db, channel):
    reply = run("schedule_pr_reminder", {"cron": "*/30 * * * *", "scope": "channel", "channel": channel}, bot)

    assert "is not a Slack channel ID" in reply
    collect.assert_not_called()


def test_schedule_pr_reminder_enforces_the_per_channel_limit(bot, collect, check, db):
    count, add, _ = db
    count.return_value = AgentConfigs.PR_REMINDER_MAX_ACTIVE_PER_CHANNEL

    reply = run("schedule_pr_reminder", {"cron": "*/30 * * * *", "scope": "channel", "channel": "C2OTHER99"}, bot)

    assert "already has 5 active PR reminders" in reply
    count.assert_called_once_with("bot-a", "C2OTHER99")
    collect.assert_not_called()


# --- errors ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code, expected",
    [("not_in_channel", "Invite me"), ("missing_scope", "channels:history"), ("is_archived", "archived"), ("weird", "Slack error: weird")],
)
def test_channel_read_errors_become_a_readable_message(bot, collect, code, expected):
    collect.side_effect = ChannelReadError(code)

    assert expected in run("check_pr_approvals", {"scope": "channel"}, bot)


def test_unexpected_errors_are_returned_to_the_agent_not_raised(bot, collect):
    collect.side_effect = RuntimeError("github down")

    assert run("check_pr_approvals", {"scope": "channel"}, bot) == "Error executing check_pr_approvals: github down"


def test_an_unexpected_value_error_is_reported_as_an_error_not_as_a_validation_message(bot, collect, caplog):
    collect.side_effect = ValueError("could not convert string to float: 'x'")

    reply = run("check_pr_approvals", {"scope": "channel"}, bot)

    assert reply == "Error executing check_pr_approvals: could not convert string to float: 'x'"
    assert "check_pr_approvals failed" in caplog.text


def test_unknown_tool_name(bot):
    assert run("nope", {}, bot) == "Unknown PR reminder tool: nope"

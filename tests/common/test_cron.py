from datetime import UTC, datetime

import pytest

from common.cron import CronError, validate_cron

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("cron", ["0 * * * *", "0 2 * * *", "*/60 * * * *"])
def test_a_cron_at_or_above_the_minimum_interval_is_accepted(cron):
    validate_cron(cron, 60, "A dataset sync", NOW)


def test_a_cron_below_the_minimum_names_the_subject_and_the_minimum():
    with pytest.raises(CronError, match="A dataset sync can run at most every 60 minutes"):
        validate_cron("*/30 * * * *", 60, "A dataset sync", NOW)


def test_the_shortest_gap_across_midnight_counts():
    with pytest.raises(CronError, match="at most every 60"):
        validate_cron("0,40 0,2,23 * * *", 60, "A dataset sync", NOW)


@pytest.mark.parametrize("cron", ["", "* * * *", "61 * * * *", "0 0 31 2 *"])
def test_malformed_crons_are_rejected(cron):
    with pytest.raises(CronError):
        validate_cron(cron, 60, "A dataset sync", NOW)

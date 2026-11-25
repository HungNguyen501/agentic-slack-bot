import pytest

from channel_analysis.core.read_scope import ReadScope

CURRENT = "C0CURRENT01"


def scope(request="how many reports failed in #alerts last week?"):
    return ReadScope(request, CURRENT)


@pytest.mark.parametrize(
    "channel",
    ["alerts", "#alerts", "Alerts", "<#C0ALERTS001|alerts>", CURRENT],
    ids=["name", "hash-name", "case", "mention-link", "current-channel"],
)
def test_a_channel_the_request_names_or_was_made_in_is_allowed(channel):
    assert scope("how many reports failed in <#C0ALERTS001|alerts> or #alerts?").permits(channel)


@pytest.mark.parametrize("channel", ["secret-finance", "C0SECRET001", "<#C0SECRET001|secret-finance>", "alert", "", None, "D0DIRECT001"])
def test_a_channel_the_request_does_not_name_is_refused(channel):
    assert not scope().permits(channel)


def test_a_name_that_is_only_part_of_a_longer_word_in_the_request_is_not_named():
    assert not scope("check #alerts-prod").permits("alerts")


def test_a_channel_link_in_the_request_allows_its_id():
    assert scope("read https://acme.slack.com/archives/C0LINKED001/p1700000000000000").permits("C0LINKED001")


def test_a_channel_named_before_the_first_read_stays_allowed_by_name_and_by_the_id_a_check_resolved_it_to():
    read_scope = scope("compare the two")

    read_scope.trust("billing", "Channel C0BILLING01: bot can read it.")

    assert read_scope.permits("billing") and read_scope.permits("C0BILLING01")
    assert not read_scope.permits("C0OTHER0001")


def test_a_read_result_is_not_a_source_of_trusted_channels():
    read_scope = scope("read #alerts")

    read_scope.trust("alerts")  # a read's own result is never passed in

    assert not read_scope.permits("C0SECRET001")

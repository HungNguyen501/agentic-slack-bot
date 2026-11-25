import pytest

from channel_analysis.core.read_scope import ReadScope

CURRENT = "C0CURRENT01"


def scope(request="how many reports failed in #alerts last week?"):
    return ReadScope(request, CURRENT)


@pytest.mark.parametrize(
    "channel",
    ["alerts", "#alerts", "Alerts", "C0ALERTS001", "<#C0ALERTS001|alerts>", CURRENT],
    ids=["name", "hash-name", "case", "id-of-mention", "mention-link", "current-channel"],
)
def test_a_channel_the_request_points_at_or_was_made_in_is_allowed(channel):
    assert scope("how many reports failed in <#C0ALERTS001|alerts> or #alerts?").permits(channel)


@pytest.mark.parametrize("channel", ["secret-finance", "C0SECRET001", "<#C0SECRET001|secret-finance>", "alert", "", None, "D0DIRECT001"])
def test_a_channel_the_request_does_not_name_is_refused(channel):
    assert not scope().permits(channel)


def test_a_topic_word_that_is_also_a_channel_name_is_not_a_choice_of_channel():
    read_scope = ReadScope("summarize billing complaints in #support", CURRENT)

    assert read_scope.permits("support") and not read_scope.permits("billing") and not read_scope.permits("#billing")


def test_a_name_that_is_only_part_of_a_longer_channel_name_in_the_request_is_not_named():
    assert not scope("check #alerts-prod").permits("alerts")


def test_a_channel_link_in_the_request_allows_its_id():
    assert scope("read https://acme.slack.com/archives/C0LINKED001/p1700000000000000").permits("C0LINKED001")


def test_a_channel_named_before_the_first_read_stays_allowed_by_name():
    read_scope = scope("compare the two")

    read_scope.trust("billing")
    read_scope.trust("C0BILLING01")

    assert read_scope.permits("billing") and read_scope.permits("C0BILLING01")
    assert not read_scope.permits("C0OTHER0001")


def test_the_id_an_access_check_resolved_a_name_to_is_allowed():
    read_scope = scope("compare #alpha and #beta")

    read_scope.trust_resolved("Access check for C0BETA0001: OK. I can read it and you may see it.\n- warning: see C0OTHER0001")

    assert read_scope.permits("C0BETA0001") and not read_scope.permits("C0OTHER0001")


def test_only_the_channel_a_check_names_on_its_first_line_is_taken_not_ids_in_its_other_lines():
    read_scope = scope("compare #alpha and #beta")

    read_scope.trust_resolved("Access check for C0BETA0001: OK.\n- warning: Slack said C0OTHER0001")
    read_scope.trust_resolved("not a check result C0THIRD0001")

    assert read_scope.permits("C0BETA0001")
    assert not read_scope.permits("C0OTHER0001") and not read_scope.permits("C0THIRD0001")


def test_a_number_after_a_hash_is_not_a_channel_name_but_a_numbered_channel_in_a_mention_still_is():
    read_scope = ReadScope("see PR #42, and <#C0NUMBER001|2024>", CURRENT)

    assert not read_scope.permits("42") and read_scope.permits("2024")

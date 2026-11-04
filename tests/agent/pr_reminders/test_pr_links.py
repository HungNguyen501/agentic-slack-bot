import pytest

from agent.pr_reminders.pr_links import PrLink, PrRef, find_pr_refs, limit_links, links_from_messages, links_from_urls, merge_links

PR = PrRef("org", "repo", 1)


def link(number, message_ts, thread_ts="1.0", channel="C1"):
    return PrLink(PrRef("org", "repo", number), channel, thread_ts, message_ts)


def test_find_pr_refs_reads_plain_and_slack_wrapped_links():
    text = "see https://github.com/org/repo/pull/1 and <https://github.com/org/other/pull/22|fix the thing>"

    assert find_pr_refs(text) == [PR, PrRef("org", "other", 22)]


def test_find_pr_refs_dedupes_across_casing_and_trailing_paths():
    text = "https://github.com/Org/Repo/pull/1 https://github.com/org/repo/pull/1/files"

    assert find_pr_refs(text) == [PR]


@pytest.mark.parametrize(
    "text",
    ["https://github.com/org/repo/issues/1", "https://github.com/org/repo/pull/", "https://gitlab.com/org/repo/pull/1", "no links here", ""],
    ids=["issue", "no-number", "other-host", "plain-text", "empty"],
)
def test_find_pr_refs_ignores_things_that_are_not_pr_links(text):
    assert find_pr_refs(text) == []


def test_find_pr_refs_ignores_a_number_too_large_to_be_a_pr_in_a_graphql_query():
    # GraphQL's Int is 32-bit: one such number would make GitHub reject the whole batch.
    text = "https://github.com/org/repo/pull/99999999999 https://github.com/org/repo/pull/2147483647 https://github.com/org/repo/pull/2147483648"

    assert find_pr_refs(text) == [PrRef("org", "repo", 2147483647)]


@pytest.mark.parametrize(
    "digits",
    ["9" * 4301, "9" * 10_000, "1" + "0" * 50, "0" * 4400 + "7"],
    ids=["4301-digits", "10000-digits", "50-digit-number", "leading-zeros-then-7"],
)
def test_find_pr_refs_survives_absurdly_long_numbers_and_still_finds_the_valid_link(digits):
    text = f"https://github.com/org/repo/pull/{digits} and https://github.com/org/repo/pull/12"

    refs = find_pr_refs(text)

    assert PrRef("org", "repo", 12) in refs
    assert all(ref.number <= 2**31 - 1 for ref in refs)


def test_find_pr_refs_reads_a_number_written_with_leading_zeros():
    assert find_pr_refs("https://github.com/org/repo/pull/0007") == [PrRef("org", "repo", 7)]


def test_find_pr_refs_ignores_pull_zero():
    assert find_pr_refs("https://github.com/org/repo/pull/0 https://github.com/org/repo/pull/000") == []


def test_links_from_messages_finds_links_posted_as_attachments_and_blocks():
    messages = [
        {"ts": "1.0", "text": "", "attachments": [{"title_link": "https://github.com/org/repo/pull/1"}]},
        {"ts": "2.0", "blocks": [{"type": "rich_text", "elements": [{"type": "link", "url": "https://github.com/org/repo/pull/2"}]}]},
    ]

    links = links_from_messages(messages, "C1", own_app_id=None)

    assert [link.ref.number for link in links] == [1, 2]


def test_links_from_messages_uses_the_messages_own_thread_or_starts_one_on_it():
    messages = [
        {"ts": "5.0", "text": "https://github.com/org/repo/pull/1"},
        {"ts": "6.0", "thread_ts": "6.0", "text": "https://github.com/org/repo/pull/2"},
        {"ts": "7.5", "thread_ts": "7.0", "text": "https://github.com/org/repo/pull/3"},
    ]

    links = {link.ref.number: link for link in links_from_messages(messages, "C1", own_app_id=None)}

    assert links[1].thread_ts == "5.0"
    assert links[2].thread_ts == "6.0"
    assert links[3].thread_ts == "7.0"
    assert links[3].message_ts == "7.5"


def test_links_from_messages_keeps_the_most_recent_message_for_a_pr():
    messages = [
        {"ts": "9.0", "text": "https://github.com/org/repo/pull/1"},
        {"ts": "3.0", "text": "https://github.com/org/repo/pull/1"},
        {"ts": "12.0", "text": "https://github.com/org/repo/pull/1"},
    ]

    [only] = links_from_messages(messages, "C1", own_app_id=None)

    assert only.message_ts == "12.0"


def test_links_from_messages_skips_the_bots_own_messages():
    messages = [
        {"ts": "1.0", "app_id": "A_BOT", "text": "https://github.com/org/repo/pull/1"},
        {"ts": "2.0", "app_id": "A_OTHER", "text": "https://github.com/org/repo/pull/2"},
    ]

    links = links_from_messages(messages, "C1", own_app_id="A_BOT")

    assert [link.ref.number for link in links] == [2]


def test_links_from_urls_puts_typed_links_in_the_request_thread():
    [typed] = links_from_urls(["https://github.com/org/repo/pull/1"], "C1", "4.0")

    assert typed == PrLink(PR, "C1", "4.0", "4.0")


def test_merge_links_lets_a_later_group_replace_the_same_pr():
    scanned = [link(1, "1.0", thread_ts="1.0"), link(2, "2.0")]
    typed = [link(1, "9.0", thread_ts="9.0")]

    merged = {m.ref.number: m for m in merge_links(scanned, typed)}

    assert merged[1].thread_ts == "9.0"
    assert set(merged) == {1, 2}


def test_limit_links_keeps_the_newest_and_counts_the_rest():
    links = [link(1, "1.0"), link(2, "3.0"), link(3, "2.0")]

    kept, dropped = limit_links(links, 2)

    assert [k.ref.number for k in kept] == [2, 3]
    assert dropped == 1


def test_limit_links_drops_nothing_under_the_limit():
    assert limit_links([link(1, "1.0")], 5)[1] == 0

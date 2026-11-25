"""The size estimate that fills a page (core) must cover what the page really shows (presentation)."""
import pytest

from channel_analysis.core import limits
from channel_analysis.core.models import ChannelMessage, FileRef
from channel_analysis.presentation.tools import _format_message, _format_slice
from tests.support.channel_analysis import BOT, CHANNEL, make_message

ADMIN = "UADMIN"


def rendered(message: ChannelMessage) -> int:
    return len("\n".join(_format_message(message)))


def lines(count: int, word: str = "x") -> str:
    return "\n".join(word for _ in range(count))


SHAPES = {
    "plain": make_message(1, "a report"),
    "multiline": make_message(1, lines(300)),
    "reactions": make_message(1, "x", reactions=tuple((f"custom_emoji_{n}", 120) for n in range(60))),
    "files": make_message(1, "x", files=tuple(FileRef(f"F{n}", f"report_{n}.csv", "csv", 5000) for n in range(10))),
    "multiline reply": make_message(1, "root", replies=(make_message(0.5, lines(1500), author="<@U1>"),), reply_count=1),
    "three multiline replies": make_message(1, "root", replies=tuple(make_message(0.5, lines(1500), author="<@U1>") for _ in range(3)), reply_count=3),
    "reply with files and reactions": make_message(
        1, "root", reply_count=1, replies=(make_message(0.5, lines(20), files=(FileRef("F1", "late.csv", "csv", 9),), reactions=(("eyes", 2), ("rocket", 1))),)
    ),
    "omitted replies": make_message(1, "root", reply_count=50, omitted_replies=40, replies=(make_message(0.5, "one"),)),
}  # fmt: skip


@pytest.mark.parametrize("name", SHAPES)
def test_what_a_message_renders_to_never_exceeds_the_size_that_filled_the_page(name):
    message = SHAPES[name]

    assert rendered(message) <= message.size


@pytest.mark.parametrize("budget", [limits.PAGE_CHAR_BUDGET, 20_000, 12_000])
def test_four_threads_of_long_multiline_replies_fill_pages_that_stay_within_the_budget(service, history, monkeypatch, budget):
    monkeypatch.setattr(limits, "PAGE_CHAR_BUDGET", budget)
    reply = lambda: make_message(0.5, lines(1500), author="<@U1>")  # noqa: E731
    history.messages = [make_message(n, f"root {n}", replies=(reply(), reply(), reply()), reply_count=3) for n in range(1, 5)]
    pages, before = [], None

    while True:
        page = service.read(BOT, CHANNEL, ADMIN, before=before)
        pages.append(page)
        before = page.next_before
        if before is None:
            break

    header_allowance = 1000
    assert all(len(_format_slice(page)) <= budget + header_allowance for page in pages)
    assert sum(len(page.messages) for page in pages) == 4


def test_a_thread_of_multiline_replies_is_cut_to_what_one_thread_may_add_counting_the_indent(service, history):
    replies = tuple(make_message(0.5, lines(1500), author="<@U1>") for _ in range(6))
    history.messages = [make_message(1, "root", replies=replies, reply_count=6)]

    message = service.read(BOT, CHANNEL, ADMIN).messages[0]

    shown = len("\n".join(_format_message(message)))
    assert len(message.replies) < 6 and message.omitted_replies == 6 - len(message.replies)
    assert shown <= limits.REPLIES_CHAR_LIMIT + limits.MESSAGE_CHAR_LIMIT + 3000

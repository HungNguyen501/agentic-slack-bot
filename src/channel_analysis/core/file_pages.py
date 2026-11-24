from channel_analysis.core.limits import HEADER_MAX_PAGE_SHARE


def cut_page(text: str, offset: int, page_chars: int, header: str | None = None) -> tuple[str, int | None]:
    """Cut one page out of `text`, starting at `offset`, whole lines only (a longer line than a page is cut anyway).

    With a `header` (a CSV's first line) every page after the first starts with it again, so a page can be read alone. A header
    that would take more than `HEADER_MAX_PAGE_SHARE` of a page is not repeated: it would leave no room, and the pages would not advance.

    Returns:
        The page and the offset the next one starts at, or None when the text ends in this page.
    """
    if offset >= len(text):
        return "", None
    repeat = bool(header) and offset > 0 and len(header) + 1 <= page_chars * HEADER_MAX_PAGE_SHARE
    prefix = f"{header}\n" if repeat else ""
    budget = max(page_chars - len(prefix), 1)
    end = offset + budget
    if end >= len(text):
        return prefix + text[offset:], None
    newline = text.rfind("\n", offset, end + 1)
    if newline > offset:
        end = newline + 1
    return prefix + text[offset:end], end

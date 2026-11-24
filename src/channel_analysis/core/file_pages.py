from channel_analysis.core.limits import HEADER_MAX_PAGE_SHARE, TABULAR_FILE_TYPES


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


def sheet_header(text: str, offset: int) -> str | None:
    """For the text of a workbook (`## Sheet: name` then that sheet's rows), the sheet's name line and its first row, which a
    page starting at `offset` needs to be read alone; None when the page starts at or before that first row."""
    marker = "## Sheet: "
    # The bound lets a marker that starts exactly at `offset` count: a page that begins a new sheet already has its name.
    start = text.rfind(marker, 0, offset + len(marker))
    while start > 0 and text[start - 1] != "\n":
        start = text.rfind(marker, 0, start)
    if start < 0:
        return None
    name_end = text.find("\n", start)
    header_end = text.find("\n", name_end + 1) if name_end >= 0 else -1
    if header_end < 0 or offset <= header_end:
        return None
    return text[start:header_end]


def repeated_header(text: str, offset: int, filetype: str) -> str | None:
    """What a page of a file starting at `offset` repeats at its top: a CSV's or TSV's first line, a workbook's current sheet name and
    first row, nothing for other types."""
    kind = filetype.lower()
    if kind in TABULAR_FILE_TYPES:
        return text.split("\n", 1)[0]
    if kind == "xlsx":
        return sheet_header(text, offset)
    return None

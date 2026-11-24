from collections.abc import Iterator

from channel_analysis.core.limits import HEADER_MAX_PAGE_SHARE

_SHEET_MARKER = "## Sheet: "


def is_tabular(filetype: str) -> bool:
    """CSV and workbook text hold quoted records: a cell may contain a newline. TSV is paged by lines: it is normally not quoted, and
    a bare quote in one of its cells would hide every later line break."""
    return filetype.lower() in ("csv", "xlsx")


def _record_ends(text: str, start: int, end: int, quotes_before: int = 0) -> Iterator[int]:
    """The indices of the newlines in `text[start:end]` that end a record, that is, are not inside a quoted cell. A quote opens or
    closes a cell and a quote inside one is written twice, so an odd number of quotes so far means still inside."""
    quotes, position = quotes_before, start
    while (newline := text.find("\n", position, end)) >= 0:
        quotes += text.count('"', position, newline)
        if quotes % 2 == 0:
            yield newline
        position = newline + 1


def _record_end(text: str, start: int) -> int:
    """Index of the newline that ends the record starting at `start`, or -1 if none does."""
    return next(_record_ends(text, start, len(text)), -1)


def _last_boundary(text: str, offset: int, end: int, tabular: bool) -> int:
    """Index just after the last newline in `text[offset:end]` that ends a record (a line, if not `tabular`), after the first
    character of the page; -1 if there is none. When no record ends in the page (a cell bigger than a page, or a stray quote that
    makes the rest of the file look quoted) a line boundary is used instead of cutting through a row."""
    if tabular:
        ends = [newline for newline in _record_ends(text, offset, end, text.count('"', 0, offset)) if newline > offset]
        if ends:
            return ends[-1] + 1
    newline = text.rfind("\n", offset, end)
    return newline + 1 if newline > offset else -1


def cut_page(text: str, offset: int, page_chars: int, header: str | None = None, tabular: bool = False) -> tuple[str, int | None]:
    """Cut one page out of `text`, starting at `offset`, whole lines only (a longer line than a page is cut anyway).

    With `tabular` the page ends between records, not just lines: a quoted cell that holds a newline is never split. A record
    longer than a page is cut at a line inside it, or anywhere if it has none.

    With a `header` (a CSV's first record) every page after the first starts with it again, so a page can be read alone. A header
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
    boundary = _last_boundary(text, offset, end, tabular)
    if boundary > offset:
        end = boundary
    return prefix + text[offset:end], end


def sheet_header(text: str, offset: int) -> str | None:
    """For the text of a workbook (`## Sheet: name` then that sheet's records), the sheet's name line and its first record (all of
    it, if a cell in it holds a newline), which a page starting at `offset` needs to be read alone; None when the page starts at or
    before the end of that first record."""
    # The bound lets a marker that starts exactly at `offset` count: a page that begins a new sheet already has its name.
    start = text.rfind(_SHEET_MARKER, 0, offset + len(_SHEET_MARKER))
    while start > 0 and text[start - 1] != "\n":
        start = text.rfind(_SHEET_MARKER, 0, start)
    if start < 0:
        return None
    name_end = text.find("\n", start)
    header_end = _record_end(text, name_end + 1) if name_end >= 0 else -1
    if header_end < 0 or offset <= header_end:
        return None
    return text[start:header_end]


def repeated_header(text: str, offset: int, filetype: str) -> str | None:
    """What a page of a file starting at `offset` repeats at its top: a CSV's or TSV's first record, a workbook's current sheet name
    and first record, nothing for other types."""
    kind = filetype.lower()
    if kind == "tsv":
        return text.split("\n", 1)[0]
    if kind == "csv":
        end = _record_end(text, 0)
        return text if end < 0 else text[:end]
    if kind == "xlsx":
        return sheet_header(text, offset)
    return None

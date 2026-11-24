def cut_page(text: str, offset: int, page_chars: int, header: str | None = None) -> tuple[str, int | None]:
    """Cut one page out of `text`, starting at `offset`, whole lines only (a longer line than a page is cut anyway).

    With a `header` (a CSV's first line) every page after the first starts with it again, so a page can be read alone.

    Returns:
        The page and the offset the next one starts at, or None when the text ends in this page.
    """
    if offset >= len(text):
        return "", None
    prefix = f"{header}\n" if header and offset > 0 else ""
    budget = max(page_chars - len(prefix), 1)
    end = offset + budget
    if end >= len(text):
        return prefix + text[offset:], None
    newline = text.rfind("\n", offset, end + 1)
    if newline > offset:
        end = newline + 1
    return prefix + text[offset:end], end

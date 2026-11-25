"""HTML to text, for reports a bot posts as an `.html` file (an ingestion report, an email). Standard library only.

Scripts and styles are dropped, block elements end a line, and a table row becomes one line with its cells joined by
`ChannelAnalysisConfigs.HTML_CELL_SEPARATOR`, so a row count stays next to the name of its table.
"""
from html.parser import HTMLParser

from channel_analysis.core.configs import ChannelAnalysisConfigs


def html_to_text(data: bytes, max_chars: int) -> str:
    """The text of an HTML page, one block or table row per line. Reading stops once `max_chars` are collected."""
    builder = _TextBuilder(max_chars)
    builder.feed(_decode(data))
    builder.close()
    return builder.text()


def _decode(data: bytes) -> str:
    declared = ChannelAnalysisConfigs.HTML_CHARSET.search(data[: ChannelAnalysisConfigs.HTML_CHARSET_SCAN_BYTES])
    if declared:
        try:
            # A byte order mark stays in the text when the page declares its charset (`utf-8-sig` would drop it), and would be a line of its own.
            return data.decode(declared.group(1).decode("ascii"), errors="replace").lstrip("\ufeff")
        except LookupError:
            pass
    return data.decode("utf-8-sig", errors="replace")


class _TextBuilder(HTMLParser):
    def __init__(self, max_chars: int) -> None:
        super().__init__(convert_charrefs=True)
        self._max_chars = max_chars
        self._lines: list[str] = []
        self._length = 0
        self._pieces: list[str] = []
        self._row: list[str] = []
        self._in_cell = False
        self._skip_depth = 0
        self._pre_depth = 0

    def text(self) -> str:
        self._end_cell()
        self._end_row()
        self._end_line()
        return "\n".join(self._lines)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ChannelAnalysisConfigs.HTML_SKIPPED_TAGS:
            self._skip_depth += 1
        elif tag in ChannelAnalysisConfigs.HTML_CELL_TAGS:
            self._end_cell()
            self._in_cell = True
        elif tag in ChannelAnalysisConfigs.HTML_ROW_TAGS:
            self._end_cell()
            self._end_row()
            self._end_line()
        elif tag in ChannelAnalysisConfigs.HTML_BLOCK_TAGS:
            self._block_break()
            if tag == "pre":
                self._pre_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ChannelAnalysisConfigs.HTML_SKIPPED_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in ChannelAnalysisConfigs.HTML_CELL_TAGS:
            self._end_cell()
        elif tag in ChannelAnalysisConfigs.HTML_ROW_TAGS:
            self._end_cell()
            self._end_row()
            self._end_line()
        elif tag in ChannelAnalysisConfigs.HTML_BLOCK_TAGS:
            self._block_break()
            if tag == "pre":
                self._pre_depth = max(0, self._pre_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._skip_depth or self._length > self._max_chars:
            return
        if self._pre_depth and not self._in_cell:
            *whole, last = data.split("\n")
            for line in whole:
                self._pieces.append(line)
                self._end_line()
            self._pieces.append(last)
        else:
            self._pieces.append(data)

    def _block_break(self) -> None:
        # Inside a cell a block element (a `<p>`, a `<br>`) is only a space: the row stays one line.
        if self._in_cell:
            self._pieces.append(" ")
        else:
            self._end_line()

    def _collapsed(self) -> str:
        """The pieces as one line. Whitespace runs shrink to one space, except in `<pre>` (outside a table cell), where indentation
        means something and only the trailing whitespace goes."""
        raw = "".join(self._pieces)
        self._pieces = []
        if self._pre_depth and not self._in_cell:
            return raw.rstrip()
        return " ".join(raw.split())

    def _end_line(self) -> None:
        self._add_line(self._collapsed())

    def _end_cell(self) -> None:
        if self._in_cell:
            self._row.append(self._collapsed())
            self._in_cell = False

    def _end_row(self) -> None:
        cells = self._row
        self._row = []
        while cells and not cells[-1]:
            cells.pop()
        self._add_line(ChannelAnalysisConfigs.HTML_CELL_SEPARATOR.join(cells))

    def _add_line(self, line: str) -> None:
        if line:
            self._lines.append(line)
            self._length += len(line) + 1

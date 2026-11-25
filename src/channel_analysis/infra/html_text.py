"""HTML to text, for reports a bot posts as an `.html` file (an ingestion report, an email). Standard library only.

Scripts and styles are dropped, except a JSON data script (a report that draws its table in the browser keeps its rows there), which
is kept with one record per line. Block elements end a line, and a table row becomes one line with its cells joined by
`ChannelAnalysisConfigs.HTML_CELL_SEPARATOR`, so a row count stays next to the name of its table. A table inside a cell of another
keeps the outer row whole: the inner rows come out as their own lines (before the outer row, which ends after them).

Collecting stops once the text is longer than the limit the caller cuts at, so the caller still sees that it was cut.
"""
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from html.parser import HTMLParser

from channel_analysis.core.configs import ChannelAnalysisConfigs


def html_to_text(data: bytes, max_chars: int) -> str:
    """The text of an HTML page, one block or table row per line. Reading stops once `max_chars` are collected."""
    builder = _TextBuilder(max_chars)
    builder.feed(_decode(data))
    builder.close()
    return builder.text()


def _compact(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except RecursionError:
        return "[too deeply nested to show]"


def _data_lines(text: str) -> Iterator[str]:
    """The lines of a JSON data script, made as they are read: a list, or each list under a top-level key, has one record per line, so
    a page cut never splits a record and a reader that stops early serializes no more. Text that isn't JSON (or is nested too deeply
    to read) is kept as it is."""
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        yield from (line.strip() for line in text.splitlines() if line.strip())
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, list) and item:
                yield f"{key}:"
                yield from (_compact(record) for record in item)
            else:
                yield f"{key}: {_compact(item)}"
    elif isinstance(value, list):
        yield from (_compact(record) for record in value)
    else:
        yield _compact(value)


def _decode(data: bytes) -> str:
    declared = ChannelAnalysisConfigs.HTML_CHARSET.search(data[: ChannelAnalysisConfigs.HTML_CHARSET_SCAN_BYTES])
    if declared:
        try:
            # A byte order mark stays in the text when the page declares its charset (`utf-8-sig` would drop it), and would be a line of its own.
            return data.decode(declared.group(1).decode("ascii"), errors="replace").lstrip("\ufeff")
        except LookupError:
            pass
    return data.decode("utf-8-sig", errors="replace")


def _collapsed(pieces: list[str], keep_indent: bool) -> str:
    """The pieces as one line. Whitespace runs shrink to one space, except in `<pre>` (outside a table cell), where indentation means
    something and only the trailing whitespace goes."""
    raw = "".join(pieces)
    return raw.rstrip() if keep_indent else " ".join(raw.split())


@dataclass
class _Table:
    """A table being read: the cells of its open row, and the text pieces of its open cell (None between cells)."""

    row: list[str] = field(default_factory=list)
    cell: list[str] | None = None


class _TextBuilder(HTMLParser):
    def __init__(self, max_chars: int) -> None:
        super().__init__(convert_charrefs=True)
        self._max_chars = max_chars
        self._lines: list[str] = []
        self._joined = 0  # the length of `_lines` joined by newlines
        self._pieces: list[str] = []  # the text outside any cell, until it ends a line
        self._tables: list[_Table] = []  # the tables the reader is in, outermost first
        self._skip_depth = 0
        self._pre_depth = 0
        self._data_script: list[str] | None = None

    def text(self) -> str:
        while self._tables:
            self._end_cell()
            self._end_row()
            self._tables.pop()
        self._end_line()
        return "\n".join(self._lines)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ChannelAnalysisConfigs.HTML_SKIPPED_TAGS:
            self._skip_depth += 1
            if tag == "script" and (dict(attrs).get("type") or "").split(";")[0].strip().lower() in ChannelAnalysisConfigs.HTML_DATA_SCRIPT_TYPES:
                self._data_script = []
        elif tag == "table":
            if not self._tables:
                self._end_line()
            self._tables.append(_Table())
        elif tag in ChannelAnalysisConfigs.HTML_CELL_TAGS:
            self._ensure_table()
            self._end_cell()
            self._tables[-1].cell = []
        elif tag in ChannelAnalysisConfigs.HTML_ROW_TAGS:
            self._ensure_table()
            self._end_row()
        elif tag in ChannelAnalysisConfigs.HTML_BLOCK_TAGS:
            self._block_break()
            if tag == "pre":
                self._pre_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ChannelAnalysisConfigs.HTML_SKIPPED_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            if self._data_script is not None:
                self._add_data_script()
        elif tag == "table":
            if self._tables:
                self._end_row()
                self._tables.pop()
                if not self._tables:
                    self._end_line()
        elif tag in ChannelAnalysisConfigs.HTML_CELL_TAGS:
            self._end_cell()
        elif tag in ChannelAnalysisConfigs.HTML_ROW_TAGS:
            self._end_row()
        elif tag in ChannelAnalysisConfigs.HTML_BLOCK_TAGS:
            self._block_break()
            if tag == "pre":
                self._pre_depth = max(0, self._pre_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._data_script is not None:
            self._data_script.append(data)
            return
        if self._skip_depth or self._full():
            return
        if self._in_cell():
            self._tables[-1].cell.append(data)
        elif self._pre_depth:
            *whole, last = data.split("\n")
            for line in whole:
                self._pieces.append(line)
                self._end_line()
                if self._full():
                    return
            self._pieces.append(last)
        else:
            self._pieces.append(data)

    def _full(self) -> bool:
        """Whether the text is already longer than `max_chars`: more is not collected, and the caller sees the text is too long."""
        return self._joined > self._max_chars

    def _in_cell(self) -> bool:
        return bool(self._tables) and self._tables[-1].cell is not None

    def _ensure_table(self) -> None:
        """A cell or row tag outside a table (broken markup) still reads as one."""
        if not self._tables:
            self._end_line()
            self._tables.append(_Table())

    def _add_data_script(self) -> None:
        text = "".join(self._data_script or [])
        self._data_script = None
        self._block_break()
        for line in _data_lines(text):
            if self._full():
                break
            self._add_line(line)

    def _block_break(self) -> None:
        # Inside a cell a block element (a `<p>`, a `<br>`) is only a space: the row stays one line.
        if self._in_cell():
            self._tables[-1].cell.append(" ")
        else:
            self._end_line()

    def _end_line(self) -> None:
        pieces, self._pieces = self._pieces, []
        self._add_line(_collapsed(pieces, keep_indent=bool(self._pre_depth)))

    def _end_cell(self) -> None:
        if self._in_cell():
            table = self._tables[-1]
            table.row.append(_collapsed(table.cell, keep_indent=False))
            table.cell = None

    def _end_row(self) -> None:
        """End the open cell and row of the innermost table, and the text that stood between its cells."""
        if not self._tables:
            return
        self._end_cell()
        table = self._tables[-1]
        cells, table.row = table.row, []
        while cells and not cells[-1]:
            cells.pop()
        self._add_line(ChannelAnalysisConfigs.HTML_CELL_SEPARATOR.join(cells))
        self._end_line()

    def _add_line(self, line: str) -> None:
        if line and not self._full():
            self._joined += len(line) + (1 if self._lines else 0)
            self._lines.append(line)

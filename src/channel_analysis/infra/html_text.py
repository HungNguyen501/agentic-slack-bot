"""HTML to text, for reports a bot posts as an `.html` file (an ingestion report, an email). Standard library only.

Scripts and styles are dropped, except a JSON data script (a report that draws its table in the browser keeps its rows there), which
is kept with one record per line. Block elements end a line, and a table row becomes one line with its cells joined by
`ChannelAnalysisConfigs.HTML_CELL_SEPARATOR`, so a row count stays next to the name of its table.
"""
import json
from html.parser import HTMLParser

from channel_analysis.core.configs import ChannelAnalysisConfigs


def html_to_text(data: bytes, max_chars: int) -> str:
    """The text of an HTML page, one block or table row per line. Reading stops once `max_chars` are collected."""
    builder = _TextBuilder(max_chars)
    builder.feed(_decode(data))
    builder.close()
    return builder.text()


def _compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _data_lines(text: str) -> list[str]:
    """The lines of a JSON data script: a list, or each list under a top-level key, has one record per line, so a page cut never splits
    a record. Text that isn't JSON (or is nested too deeply to read) is kept as it is."""
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            lines: list[str] = []
            for key, item in value.items():
                if isinstance(item, list) and item:
                    lines.append(f"{key}:")
                    lines.extend(_compact(record) for record in item)
                else:
                    lines.append(f"{key}: {_compact(item)}")
            return lines
        return [_compact(record) for record in value] if isinstance(value, list) else [_compact(value)]
    except (ValueError, RecursionError):
        return [line.strip() for line in text.splitlines() if line.strip()]


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
        self._data_script: list[str] | None = None

    def text(self) -> str:
        self._end_cell()
        self._end_row()
        self._end_line()
        return "\n".join(self._lines)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ChannelAnalysisConfigs.HTML_SKIPPED_TAGS:
            self._skip_depth += 1
            if tag == "script" and (dict(attrs).get("type") or "").split(";")[0].strip().lower() in ChannelAnalysisConfigs.HTML_DATA_SCRIPT_TYPES:
                self._data_script = []
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
            if self._data_script is not None:
                self._add_data_script()
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
        if self._data_script is not None:
            self._data_script.append(data)
            return
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

    def _add_data_script(self) -> None:
        text = "".join(self._data_script or [])
        self._data_script = None
        self._end_cell()
        self._end_row()
        self._end_line()
        for line in _data_lines(text):
            self._add_line(line)

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

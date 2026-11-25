"""Turns the bytes of a file into text, by file type.

`extract_text` does the parsing. A PDF, a workbook or an HTML page is parsed through `file_parser.extract_text_isolated`, which runs this module
(`python -m channel_analysis.infra.file_text`) in a child process with memory and time limits.
"""
import csv
import io
import json
import sys
import xml.etree.ElementTree as ElementTree
import zipfile

import openpyxl
import pypdf
from openpyxl.utils.exceptions import InvalidFileException

from channel_analysis.core.configs import ChannelAnalysisConfigs
from channel_analysis.core.errors import FileUnreadableError
from channel_analysis.infra.file_kinds import kind_of
from channel_analysis.infra.html_text import html_to_text

_PARSE_ERRORS = (zipfile.BadZipFile, InvalidFileException, ElementTree.ParseError, KeyError, ValueError, OSError)


def extract_text(name: str, filetype: str, data: bytes, max_chars: int) -> str:
    """Return the text of a file, cut at `max_chars`.

    Raises:
        FileUnreadableError: an unsupported type, a PDF without text, a damaged file, or an archive that unpacks to too much.
    """
    kind = kind_of(name, filetype)
    if kind in ChannelAnalysisConfigs.PLAIN_KINDS:
        text = data.decode("utf-8-sig", errors="replace")
    elif kind in ChannelAnalysisConfigs.HTML_KINDS:
        text = html_to_text(data, max_chars)
        if not text:
            raise FileUnreadableError("That HTML file has no text in it, so I can't read it.")
    elif kind == "pdf":
        text = _pdf_text(data, max_chars)
    elif kind == "xlsx":
        text = _xlsx_text(data, max_chars)
    else:
        raise FileUnreadableError(f"I can't read `{name}` ({filetype}). I read {ChannelAnalysisConfigs.SUPPORTED_FILES_TEXT}.")
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[file cut here: it is longer than I read]"
    return text


def _pdf_text(data: bytes, max_chars: int) -> str:
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        pages: list[str] = []
        total = 0
        for page in reader.pages:
            page_text = (page.extract_text() or "").strip()
            pages.append(page_text)
            total += len(page_text)
            if total > max_chars:
                break
    except (pypdf.errors.PyPdfError, ValueError, KeyError) as exc:
        raise FileUnreadableError(f"I couldn't open that PDF ({exc.__class__.__name__}); it may be damaged or password-protected.") from exc
    text = "\n\n".join(page for page in pages if page)
    if not text:
        raise FileUnreadableError("That PDF has no text layer (it is probably a scan), so I can't read it.")
    return text


def _check_archive(data: bytes) -> None:
    """Refuse a workbook that unpacks to far more than it weighs. The sizes come from the archive's directory, and the reader
    stops at them, so they bound what parsing can read."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
    if len(members) > ChannelAnalysisConfigs.XLSX_MAX_MEMBERS or sum(member.file_size for member in members) > ChannelAnalysisConfigs.XLSX_MAX_UNCOMPRESSED_BYTES:
        raise FileUnreadableError("That Excel file unpacks to far more than I read (more than 100 MB), so I won't open it.")


def _xlsx_text(data: bytes, max_chars: int) -> str:
    # The sheets are read lazily, so a damaged one fails during the iteration below, not when the workbook opens.
    try:
        _check_archive(data)
        workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            return _sheets_text(workbook, max_chars)
        finally:
            workbook.close()
    except _PARSE_ERRORS as exc:
        raise FileUnreadableError(f"I couldn't read that Excel file ({exc.__class__.__name__}); it may be damaged.") from exc


def _sheets_text(workbook, max_chars: int) -> str:
    out = io.StringIO()
    for sheet in workbook.worksheets:
        # A quote in the name would flip the quote count that tells a record's end from a newline inside a cell, and a line break would
        # make the rest of the name look like the sheet's first record. Excel won't take either, but a crafted file can hold them.
        title = sheet.title.replace('"', "'").replace("\r", " ").replace("\n", " ")
        out.write(f"{ChannelAnalysisConfigs.SHEET_MARKER}{title}\n")
        writer = csv.writer(out, lineterminator="\n")
        # A first cell that reads like a sheet marker is quoted, so a page cut can't mistake its row for the start of a sheet.
        quoting_writer = csv.writer(out, lineterminator="\n", quoting=csv.QUOTE_ALL)
        # A generated sheet can declare a million rows by 16,000 columns in its header and hold ten: read the rows that are there.
        sheet.reset_dimensions()
        for row in sheet.iter_rows(values_only=True):
            cells = ["" if value is None else value for value in row]
            while cells and cells[-1] == "":
                cells.pop()
            if not cells:
                continue
            (quoting_writer if isinstance(cells[0], str) and cells[0].startswith(ChannelAnalysisConfigs.SHEET_MARKER) else writer).writerow(cells)
            if out.tell() > max_chars:
                return out.getvalue()
    return out.getvalue()


def _limit_resources() -> None:
    """Lower this process's memory and CPU limits (each on its own, so one that can't be set doesn't skip the other).

    Raises:
        FileUnreadableError: a limit can't be set on Linux, where the worker runs and where both are enforced: parsing an untrusted
            file with no memory bound could take the worker down, which a wall-clock timeout doesn't prevent. Elsewhere (a developer's
            laptop: macOS refuses to lower RLIMIT_AS) the parent's timeout is all there is.
    """
    try:
        import resource
    except ImportError:
        return
    failed = False
    for which, wanted in ((resource.RLIMIT_AS, ChannelAnalysisConfigs.PARSE_MEMORY_BYTES), (resource.RLIMIT_CPU, ChannelAnalysisConfigs.PARSE_CPU_SECONDS)):
        try:
            # A hard limit already below the wanted one is a boundary too: lower the soft limit to it.
            hard = resource.getrlimit(which)[1]
            limit = wanted if hard == resource.RLIM_INFINITY else min(wanted, hard)
            resource.setrlimit(which, (limit, limit))
        except (ValueError, OSError):
            failed = True
    if failed and sys.platform.startswith("linux"):
        raise FileUnreadableError(
            "I can't open files here: this host won't let me limit the memory and CPU of the file reader, and reading an untrusted file without limits isn't safe."
        )


def main(argv: list[str]) -> int:
    """Child process: the file on stdin, `name filetype max_chars` as arguments, one JSON object on stdout ({"text"} or {"error"})."""
    name, filetype, max_chars = argv[0], argv[1], int(argv[2])
    try:
        _limit_resources()
        data = sys.stdin.buffer.read()
        result = {"text": extract_text(name, filetype, data, max_chars)}
    except FileUnreadableError as exc:
        result = {"error": str(exc)}
    except MemoryError:
        result = {"error": "That file needs more memory to read than I allow."}
    sys.stdout.write(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

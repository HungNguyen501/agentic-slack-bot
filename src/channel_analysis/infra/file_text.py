"""Turns the bytes of a file into text, by file type."""
import csv
import io
import zipfile

import openpyxl
import pypdf
from openpyxl.utils.exceptions import InvalidFileException

from channel_analysis.core.errors import FileUnreadableError

_PLAIN_TYPES = frozenset({"csv", "tsv", "txt", "text", "json", "log", "md", "markdown", "yaml", "yml", "xml", "sql"})
SUPPORTED = "CSV, TSV, text, JSON, logs, PDF with a text layer, and Excel (.xlsx)"


def _kind(name: str, filetype: str) -> str:
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return extension or filetype.lower()


def extract_text(name: str, filetype: str, data: bytes, max_chars: int) -> str:
    """Return the text of a file, cut at `max_chars`.

    Raises:
        FileUnreadableError: an unsupported type, a PDF without text, or a damaged file.
    """
    kind = _kind(name, filetype)
    if kind in _PLAIN_TYPES:
        text = data.decode("utf-8-sig", errors="replace")
    elif kind == "pdf":
        text = _pdf_text(data, max_chars)
    elif kind == "xlsx":
        text = _xlsx_text(data, max_chars)
    else:
        raise FileUnreadableError(f"I can't read `{name}` ({filetype}). I read {SUPPORTED}.")
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


def _xlsx_text(data: bytes, max_chars: int) -> str:
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except (zipfile.BadZipFile, InvalidFileException, KeyError, ValueError) as exc:
        raise FileUnreadableError(f"I couldn't open that Excel file ({exc.__class__.__name__}); it may be damaged.") from exc
    out = io.StringIO()
    try:
        for sheet in workbook.worksheets:
            out.write(f"## Sheet: {sheet.title}\n")
            writer = csv.writer(out, lineterminator="\n")
            for row in sheet.iter_rows(values_only=True):
                writer.writerow("" if value is None else value for value in row)
                if out.tell() > max_chars:
                    return out.getvalue()
    finally:
        workbook.close()
    return out.getvalue()

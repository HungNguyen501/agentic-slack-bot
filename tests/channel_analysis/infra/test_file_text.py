import io
import zipfile
from unittest.mock import patch

import pytest
from pypdf import PdfWriter

from channel_analysis.core import limits
from channel_analysis.core.errors import FileUnreadableError
from channel_analysis.infra.file_text import extract_text
from tests.support.channel_analysis_files import pdf_with_text, xlsx_with

MAX = 10_000


@pytest.mark.parametrize("name, filetype", [("rows.csv", "csv"), ("log.txt", "text"), ("data", "json"), ("NOTES.MD", "markdown")])
def test_plain_types_are_decoded(name, filetype):
    assert extract_text(name, filetype, "a,b\n1,é".encode(), MAX) == "a,b\n1,é"


def test_a_byte_order_mark_is_dropped():
    assert extract_text("rows.csv", "csv", "﻿id\n1".encode(), MAX) == "id\n1"


def test_text_longer_than_the_limit_is_cut_and_says_so():
    text = extract_text("big.txt", "text", b"x" * 100, 10)

    assert text.startswith("x" * 10)
    assert "file cut here" in text


def test_a_pdf_with_a_text_layer_gives_its_text():
    assert "Hello report" in extract_text("report.pdf", "pdf", pdf_with_text("Hello report"), MAX)


def test_a_pdf_without_text_is_said_to_be_a_scan():
    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    buffer = io.BytesIO()
    writer.write(buffer)

    with pytest.raises(FileUnreadableError, match="no text layer"):
        extract_text("scan.pdf", "pdf", buffer.getvalue(), MAX)


def test_a_damaged_pdf_is_unreadable():
    with pytest.raises(FileUnreadableError, match="couldn't open that PDF"):
        extract_text("bad.pdf", "pdf", b"not a pdf at all", MAX)


def test_an_excel_file_is_read_sheet_by_sheet_as_csv_rows():
    data = xlsx_with({"Counts": [["table", "rows"], ["a", 10], ["b", None]], "Notes": [["ok"]]})

    assert extract_text("counts.xlsx", "xlsx", data, MAX) == "## Sheet: Counts\ntable,rows\na,10\nb,\n## Sheet: Notes\nok\n"


def test_an_excel_file_is_cut_at_the_limit():
    data = xlsx_with({"S": [[f"row {n}"] for n in range(200)]})

    text = extract_text("big.xlsx", "xlsx", data, 100)

    assert len(text) < 400
    assert text.startswith("## Sheet: S")


def test_a_damaged_excel_file_is_unreadable():
    with pytest.raises(FileUnreadableError, match="couldn't read that Excel"):
        extract_text("bad.xlsx", "xlsx", b"not a zip", MAX)


def damage_member(data: bytes, member: str, replacement: bytes) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            target.writestr(info.filename, replacement if info.filename == member else source.read(info.filename))
    return out.getvalue()


def test_a_workbook_that_opens_but_has_a_damaged_sheet_is_unreadable_not_a_crash():
    data = damage_member(xlsx_with({"S": [["a"], ["b"]]}), "xl/worksheets/sheet1.xml", b"<worksheet><sheetData><row><c></worksheet>")

    with pytest.raises(FileUnreadableError, match="couldn't read that Excel"):
        extract_text("damaged.xlsx", "xlsx", data, MAX)


def test_a_workbook_that_unpacks_to_far_more_than_it_weighs_is_refused_before_it_is_opened(monkeypatch):
    monkeypatch.setattr(limits, "XLSX_MAX_UNCOMPRESSED_BYTES", 1000)
    data = damage_member(xlsx_with({"S": [["a"]]}), "xl/worksheets/sheet1.xml", b"<worksheet>" + b" " * 5000 + b"</worksheet>")

    with patch("channel_analysis.infra.file_text.openpyxl.load_workbook", autospec=True) as load, pytest.raises(FileUnreadableError, match="unpacks to far more"):
        extract_text("bomb.xlsx", "xlsx", data, MAX)

    load.assert_not_called()


def test_a_workbook_with_too_many_members_is_refused(monkeypatch):
    monkeypatch.setattr(limits, "XLSX_MAX_MEMBERS", 2)

    with pytest.raises(FileUnreadableError, match="unpacks to far more"):
        extract_text("many.xlsx", "xlsx", xlsx_with({"S": [["a"]]}), MAX)


@pytest.mark.parametrize("name, filetype", [("photo.png", "png"), ("deck.pptx", "pptx"), ("doc.docx", "docx")])
def test_an_unsupported_type_says_what_is_supported(name, filetype):
    with pytest.raises(FileUnreadableError, match="I read CSV"):
        extract_text(name, filetype, b"...", MAX)

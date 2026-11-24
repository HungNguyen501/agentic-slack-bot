import io

import openpyxl
import pytest
from pypdf import PdfWriter

from channel_analysis.core.errors import FileUnreadableError
from channel_analysis.infra.file_text import extract_text

MAX = 10_000


def pdf_with_text(text: str) -> bytes:
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 100] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        None,
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = f"BT /F1 14 Tf 10 50 Td ({text}) Tj ET"
    objects[3] = f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream"
    out, offsets = "%PDF-1.4\n", []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    return out.encode()


def xlsx_with(sheets: dict[str, list[list]]) -> bytes:
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for title, rows in sheets.items():
        sheet = workbook.create_sheet(title)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


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
    with pytest.raises(FileUnreadableError, match="couldn't open that Excel"):
        extract_text("bad.xlsx", "xlsx", b"not a zip", MAX)


@pytest.mark.parametrize("name, filetype", [("photo.png", "png"), ("deck.pptx", "pptx"), ("doc.docx", "docx")])
def test_an_unsupported_type_says_what_is_supported(name, filetype):
    with pytest.raises(FileUnreadableError, match="I read CSV"):
        extract_text(name, filetype, b"...", MAX)

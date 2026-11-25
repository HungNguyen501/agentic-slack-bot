import io
import zipfile
from unittest.mock import patch

import pytest
from pypdf import PdfWriter

from channel_analysis.core.configs import ChannelAnalysisConfigs
from channel_analysis.core.errors import FileUnreadableError
from channel_analysis.core.file_pages import sheet_header
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


@pytest.mark.parametrize("text", ["C:\\temp\\file (draft)", "unbalanced ( paren", "Café résumé"], ids=["backslash-and-parens", "lone-paren", "non-ascii"])
def test_a_pdf_keeps_backslashes_parentheses_and_non_ascii_text(text):
    assert text in extract_text("report.pdf", "pdf", pdf_with_text(text), MAX)


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

    assert extract_text("counts.xlsx", "xlsx", data, MAX) == "## Sheet: Counts\ntable,rows\na,10\nb\n## Sheet: Notes\nok\n"


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
    monkeypatch.setattr(ChannelAnalysisConfigs, "XLSX_MAX_UNCOMPRESSED_BYTES", 1000)
    data = damage_member(xlsx_with({"S": [["a"]]}), "xl/worksheets/sheet1.xml", b"<worksheet>" + b" " * 5000 + b"</worksheet>")

    with patch("channel_analysis.infra.file_text.openpyxl.load_workbook", autospec=True) as load, pytest.raises(FileUnreadableError, match="unpacks to far more"):
        extract_text("bomb.xlsx", "xlsx", data, MAX)

    load.assert_not_called()


def test_a_workbook_with_too_many_members_is_refused(monkeypatch):
    monkeypatch.setattr(ChannelAnalysisConfigs, "XLSX_MAX_MEMBERS", 2)

    with pytest.raises(FileUnreadableError, match="unpacks to far more"):
        extract_text("many.xlsx", "xlsx", xlsx_with({"S": [["a"]]}), MAX)


@pytest.mark.parametrize("name, filetype", [("photo.png", "png"), ("deck.pptx", "pptx"), ("doc.docx", "docx")])
def test_an_unsupported_type_says_what_is_supported(name, filetype):
    with pytest.raises(FileUnreadableError, match="I read CSV"):
        extract_text(name, filetype, b"...", MAX)


def test_a_sheet_that_declares_a_huge_area_is_read_for_the_rows_it_has_and_without_blank_cells():
    data = xlsx_with({"S": [["table", "rows", None, None], ["a", 1, None, None], [None, None, None, None], ["b", None, None, None]]})
    data = damage_member(data, "xl/worksheets/sheet1.xml", _with_dimension(data, "A1:XFD1048576"))

    assert extract_text("huge.xlsx", "xlsx", data, MAX) == "## Sheet: S\ntable,rows\na,1\nb\n"


def _with_dimension(workbook: bytes, ref: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(workbook)) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode()
    start = sheet.index("<dimension ref=\"") + len("<dimension ref=\"")
    return (sheet[:start] + ref + sheet[sheet.index("\"", start):]).encode()


def test_a_quote_in_a_sheet_name_does_not_change_where_the_records_end():
    data = xlsx_with({'Q"1 report': [["note"], ["line one\nline two"], ["plain"]]})

    text = extract_text("q.xlsx", "xlsx", data, MAX)

    assert text.startswith("## Sheet: Q'1 report\n")
    assert text.count('"') % 2 == 0


def test_a_supported_file_with_an_odd_suffix_is_read_by_its_slack_filetype():
    assert "Hello report" in extract_text("report.pdf.backup", "pdf", pdf_with_text("Hello report"), MAX)
    assert extract_text("rows.backup", "csv", b"id\n1\n", MAX) == "id\n1\n"
    assert extract_text("counts.old", "xlsx", xlsx_with({"S": [["a"]]}), MAX) == "## Sheet: S\na\n"


def test_a_first_cell_that_reads_like_a_sheet_marker_is_quoted_so_it_is_not_taken_for_a_sheet():
    data = xlsx_with({"Real": [["id", "note"], ["## Sheet: Revenue", 1], [2, "b"]]})

    text = extract_text("report.xlsx", "xlsx", data, MAX)

    assert text == '## Sheet: Real\nid,note\n"## Sheet: Revenue","1"\n2,b\n'


def test_a_sheet_name_with_line_breaks_stays_on_one_line_so_the_first_record_is_the_real_header():
    data = xlsx_with({"Sales\r\n2026": [["table", "rows"], ["a", 1]]})

    text = extract_text("sales.xlsx", "xlsx", data, MAX)

    assert text == "## Sheet: Sales  2026\ntable,rows\na,1\n"
    assert sheet_header(text, text.index("a,1")) == "## Sheet: Sales  2026\ntable,rows"


def test_a_cell_with_a_carriage_return_stays_one_quoted_cell():
    # The workbook format turns a lone CR into a newline when it is read; either way the cell is quoted.
    data = xlsx_with({"S": [["id", "note"], [1, "first\rsecond"]]})

    assert extract_text("r.xlsx", "xlsx", data, MAX) == '## Sheet: S\nid,note\n1,"first\nsecond"\n'


REPORT_HTML = b"""<!doctype html><html><head><meta charset="utf-8"><title>Mail ingestion report</title>
<style>td { color: red }</style><script>var rows = 99;</script></head>
<body><h1>Report &amp; totals</h1><p>Run   finished<br>on time</p>
<table><tr><th>table</th><th>rows</th></tr>
<tr><td>bridgewell_com.orders</td><td>0</td></tr>
<tr><td>bridgewell_com.items</td><td> 1,204 </td><td></td></tr>
<tr><td></td></tr></table><pre>line one
  line two</pre></body></html>"""


def test_an_html_report_gives_its_text_with_one_table_row_per_line():
    text = extract_text("mail_ingestion_report.html", "html", REPORT_HTML, MAX)

    assert text == (
        "Mail ingestion report\nReport & totals\nRun finished\non time\n"
        "table | rows\nbridgewell_com.orders | 0\nbridgewell_com.items | 1,204\nline one\nline two"
    )


def test_html_scripts_and_styles_are_not_text():
    text = extract_text("page.html", "html", REPORT_HTML, MAX)

    assert "var rows" not in text
    assert "color: red" not in text


def test_a_declared_charset_is_used_and_an_unknown_one_falls_back_to_utf8():
    latin = '<meta charset="iso-8859-1"><p>Café</p>'.encode("latin-1")
    unknown = '<meta charset="no-such-charset"><p>Café</p>'.encode()

    assert extract_text("a.html", "html", latin, MAX) == "Café"
    assert extract_text("b.html", "html", unknown, MAX) == "Café"


def test_a_byte_order_mark_before_a_declared_charset_is_not_a_line():
    assert extract_text("a.html", "html", b'\xef\xbb\xbf<meta charset="utf-8"><p>Hi</p>', MAX) == "Hi"


def test_a_slack_html_filetype_is_read_whatever_the_name():
    assert extract_text("report", "html", b"<p>hi</p>", MAX) == "hi"


def test_an_html_page_with_no_text_is_unreadable():
    with pytest.raises(FileUnreadableError, match="no text"):
        extract_text("empty.html", "html", b"<html><script>x()</script><table><tr><td></td></tr></table></html>", MAX)


def test_html_longer_than_the_limit_is_cut_and_says_so():
    text = extract_text("big.html", "html", b"<p>" + b"x" * 100 + b"</p><p>" + b"y" * 100 + b"</p>", 10)

    assert "file cut here" in text

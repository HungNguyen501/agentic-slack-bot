import pytest

from channel_analysis.infra.file_kinds import kind_of


@pytest.mark.parametrize(
    "name, filetype, kind",
    [
        ("rows.csv", "csv", "csv"),
        ("Report.PDF", "pdf", "pdf"),
        ("mail_ingestion_report.HTML", "html", "html"),
        ("report.htm", "binary", "htm"),
        ("counts.xlsx", "xlsx", "xlsx"),
        ("notes.txt", "text", "txt"),
        ("report.pdf.backup", "pdf", "pdf"),
        ("export_final", "csv", "csv"),
        ("download", "pdf", "pdf"),
        ("data.csv", "text", "csv"),
        ("report.v2.pdf", "binary", "pdf"),
        ("photo.png", "png", "png"),
        ("photo", "unknown", "unknown"),
        ("archive.zip.bak", "zip", "bak"),
    ],
)
def test_a_known_extension_wins_then_a_known_slack_filetype_then_whatever_there_is(name, filetype, kind):
    assert kind_of(name, filetype) == kind

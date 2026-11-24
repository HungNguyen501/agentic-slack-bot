import pytest

from channel_analysis.core.file_pages import cut_page, repeated_header, sheet_header


def test_a_text_that_fits_is_one_page_with_no_next():
    assert cut_page("a\nb\n", 0, 100) == ("a\nb\n", None)


def test_a_page_ends_at_a_line_boundary_and_the_next_starts_after_it():
    text = "aaaa\nbbbb\ncccc\n"

    page, next_offset = cut_page(text, 0, 12)

    assert page == "aaaa\nbbbb\n"
    assert next_offset == 10
    assert cut_page(text, next_offset, 12) == ("cccc\n", None)


def test_every_page_but_the_first_repeats_the_header():
    text = "id,n\n1,a\n2,b\n3,c\n"

    first, offset = cut_page(text, 0, 13, header="id,n")
    second, _ = cut_page(text, offset, 30, header="id,n")

    assert first == "id,n\n1,a\n2,b\n"
    assert second == "id,n\n3,c\n"


def test_a_line_longer_than_a_page_is_cut_anyway():
    page, next_offset = cut_page("x" * 50, 0, 20)

    assert page == "x" * 20
    assert next_offset == 20


def test_an_offset_past_the_end_gives_an_empty_page():
    assert cut_page("abc", 3, 10) == ("", None)


def test_paging_through_a_text_gives_every_character_once():
    text = "".join(f"row {n}\n" for n in range(40))
    out, offset = [], 0

    while offset is not None:
        page, offset = cut_page(text, offset, 50)
        out.append(page)

    assert "".join(out) == text


def test_a_header_longer_than_a_quarter_of_a_page_is_not_repeated_so_every_page_still_advances():
    header = "h" * 40
    text = header + "\n" + "".join(f"{n},x\n" for n in range(60))
    out, offset, pages = [text[: len(header) + 1]], len(header) + 1, 0

    while offset is not None:
        page, next_offset = cut_page(text, offset, 100, header=header)
        assert not page.startswith(header)
        assert next_offset is None or next_offset > offset
        out.append(page)
        offset, pages = next_offset, pages + 1

    assert "".join(out) == text
    assert pages > 1


def test_a_first_line_longer_than_a_page_does_not_stall_the_pages_that_follow():
    text = "x" * 500 + "\n" + "a,b\n" * 20

    first, offset = cut_page(text, 0, 100, header="x" * 500)
    seen = [first]
    while offset is not None:
        page, offset = cut_page(text, offset, 100, header="x" * 500)
        seen.append(page)

    assert "".join(seen) == text


WORKBOOK = "## Sheet: Counts\ntable,rows\na,1\nb,2\n## Sheet: Failures\ntable,error\nc,boom\nd,bang\n"


def test_a_page_of_a_workbook_in_the_second_sheet_needs_that_sheets_name_and_columns():
    offset = WORKBOOK.index("d,bang")

    assert sheet_header(WORKBOOK, offset) == "## Sheet: Failures\ntable,error"


def test_a_page_in_the_first_sheet_needs_the_first_sheets_header():
    assert sheet_header(WORKBOOK, WORKBOOK.index("b,2")) == "## Sheet: Counts\ntable,rows"


@pytest.mark.parametrize("where", ["## Sheet: Counts", "table,rows", "## Sheet: Failures", "table,error"], ids=["name", "header", "second-name", "second-header"])
def test_a_page_that_starts_at_or_before_a_sheets_header_repeats_nothing(where):
    assert sheet_header(WORKBOOK, WORKBOOK.index(where)) is None


def test_a_sheet_marker_inside_a_cell_is_not_taken_for_a_sheet():
    text = "## Sheet: Real\ncol\nsay ## Sheet: Fake\nnext\nmore\n"

    assert sheet_header(text, text.index("more")) == "## Sheet: Real\ncol"


@pytest.mark.parametrize(
    "filetype, expected",
    [("csv", "a,b"), ("TSV", "a,b"), ("xlsx", "## Sheet: S\na,b"), ("text", None), ("pdf", None)],
)
def test_the_header_a_page_repeats_depends_on_the_kind_of_file(filetype, expected):
    text = "a,b\n1,2\n" if filetype.lower() in ("csv", "tsv") else "## Sheet: S\na,b\n1,2\n3,4\n"

    assert repeated_header(text, len(text) - 4, filetype) == expected


def test_a_page_whose_line_ends_exactly_at_the_budget_does_not_take_the_newline_over_it():
    page, next_offset = cut_page("abcd\nx", 0, 4)

    assert len(page) <= 4
    assert (page, next_offset) == ("abcd", 4)


@pytest.mark.parametrize("budget", [4, 5, 6, 9, 10, 17, 40])
def test_no_page_is_longer_than_its_budget_with_or_without_a_repeated_header(budget):
    text = "id,n\n" + "".join(f"{n},ab\n" for n in range(30))
    for header in (None, "id,n"):
        offset, longest, pages = 0, 0, []
        while offset is not None:
            page, offset = cut_page(text, offset, budget, header)
            longest = max(longest, len(page))
            pages.append(page)
            assert len(pages) < 500
        assert longest <= budget

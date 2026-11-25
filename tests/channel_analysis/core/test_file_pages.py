import pytest

from channel_analysis.core.file_pages import cut_page, is_tabular, repeated_header, sheet_header


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


@pytest.mark.parametrize("where", ["## Sheet: Counts", "## Sheet: Failures"], ids=["name", "second-name"])
def test_a_page_that_starts_at_a_sheets_name_repeats_nothing(where):
    assert sheet_header(WORKBOOK, WORKBOOK.index(where)) is None


@pytest.mark.parametrize(
    "where, name", [("table,rows", "## Sheet: Counts"), ("table,error", "## Sheet: Failures")], ids=["header", "second-header"]
)
def test_a_page_that_starts_at_a_sheets_columns_repeats_the_sheet_name(where, name):
    """The page before ended right after the name line, so this page has the columns but not the sheet."""
    assert sheet_header(WORKBOOK, WORKBOOK.index(where)) == name


def test_a_page_cut_after_the_sheet_name_keeps_the_name_on_the_next_page():
    text = "## Sheet: Counts\n" + "a" * 60 + ",b\n1,2\n"

    first, offset = cut_page(text, 0, 40, repeated_header(text, 0, "xlsx"), True)
    second, _ = cut_page(text, offset, 200, repeated_header(text, offset, "xlsx"), True)

    assert first == "## Sheet: Counts\n"
    assert second.startswith("## Sheet: Counts\n" + "a" * 60)


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


# --- a record can hold a newline inside a quoted cell


def multiline_rows(count: int) -> str:
    return "id,note\n" + "".join(f'{n},"first line {n}\nsecond line {n}"\n' for n in range(count))


def pages_of(text: str, budget: int, header: str | None = None, tabular: bool = True) -> list[str]:
    offset, pages = 0, []
    while offset is not None:
        page, offset = cut_page(text, offset, budget, header, tabular)
        pages.append(page)
        assert len(pages) < 1000
    return pages


@pytest.mark.parametrize("filetype, expected", [("csv", True), ("CSV", True), ("xlsx", True), ("tsv", False), ("text", False), ("pdf", False), ("json", False)])
def test_which_files_are_made_of_records(filetype, expected):
    assert is_tabular(filetype) is expected


def test_a_page_of_records_never_splits_a_quoted_cell_that_holds_a_newline():
    text = multiline_rows(40)

    pages = pages_of(text, 80)

    assert len(pages) > 5
    assert "".join(pages) == text
    assert all(page.count('"') % 2 == 0 for page in pages)
    assert all(len(page) <= 80 for page in pages)


def test_the_same_text_paged_by_lines_would_have_split_a_cell_which_is_why_tabular_files_are_paged_by_records():
    text = multiline_rows(40)

    by_lines = pages_of(text, 80, tabular=False)

    assert any(page.count('"') % 2 == 1 for page in by_lines)


def test_a_header_that_spans_lines_is_repeated_whole():
    text = 'id,"note\n(free text)"\n' + "".join(f"{n},ab\n" for n in range(40))

    header = repeated_header(text, 50, "csv")

    assert header == 'id,"note\n(free text)"'
    pages = pages_of(text, 120, header)
    assert all(page.startswith(header + "\n") for page in pages[1:])
    assert all(page.count('"') % 2 == 0 for page in pages)


def test_a_one_line_csv_header_is_still_the_first_line():
    assert repeated_header("id,n\n1,2\n", 6, "csv") == "id,n"
    assert repeated_header("id,n", 0, "csv") == "id,n"


def test_a_record_longer_than_a_page_is_cut_anyway_and_the_next_pages_find_their_place():
    big = '"' + "x\n" * 300 + '"'
    text = f"id,note\n1,{big}\n2,short\n3,short\n"

    pages = pages_of(text, 100)

    assert "".join(pages) == text
    assert all(len(page) <= 100 for page in pages)
    assert pages[-1].endswith("3,short\n")


def test_a_page_starting_in_the_middle_of_a_quoted_cell_knows_it_is_inside_one():
    text = 'a,"one\ntwo\nthree\nfour"\nb,x\n'
    offset = text.index("two")

    page, next_offset = cut_page(text, offset, len(text) - offset - 3, None, True)

    assert page == 'two\nthree\nfour"\n'
    assert text[next_offset:] == "b,x\n"


def test_a_workbooks_multiline_first_record_is_repeated_whole_under_its_sheet_name():
    text = '## Sheet: S\nid,"note\nmore"\n1,a\n2,b\n'

    assert sheet_header(text, text.index("2,b")) == '## Sheet: S\nid,"note\nmore"'
    assert sheet_header(text, text.index("more")) == "## Sheet: S"


# --- a stray quote must not cost a file its row boundaries


def page_ends(text: str, budget: int, tabular: bool) -> list[str]:
    return [page[-1:] for page in pages_of(text, budget, tabular=tabular)[:-1]]


STRAY = 'id\tnote\n1\t5" pipe\n' + "".join(f"{n}\tplain note {n}\n" for n in range(2, 30))


def test_a_tsv_is_paged_by_lines_so_a_bare_quote_in_a_cell_cannot_hide_the_line_breaks():
    assert is_tabular("tsv") is False

    ends = page_ends(STRAY, 60, tabular=is_tabular("tsv"))

    assert ends and set(ends) == {"\n"}


def test_a_csv_with_a_stray_quote_falls_back_to_line_boundaries_instead_of_cutting_through_rows():
    text = STRAY.replace("\t", ",")

    ends = page_ends(text, 60, tabular=True)
    pages = pages_of(text, 60, tabular=True)

    assert ends and set(ends) == {"\n"}
    assert "".join(pages) == text and all(len(page) <= 60 for page in pages)


def test_a_cell_bigger_than_a_page_is_cut_at_a_line_inside_it_not_in_the_middle_of_one():
    cell = "".join(f"line {n:03d} of the cell\n" for n in range(40))
    text = f'id,note\n1,"{cell}"\n2,short\n'

    pages = pages_of(text, 100, tabular=True)

    assert "".join(pages) == text
    assert all(page.endswith("\n") for page in pages)
    assert all(len(page) <= 100 for page in pages)


def test_a_record_that_still_ends_inside_the_page_is_preferred_to_a_line_inside_a_cell():
    text = 'a,"one\ntwo"\nb,x\nc,"three\nfour"\n'

    page, _ = cut_page(text, 0, 20, None, True)

    assert page == 'a,"one\ntwo"\nb,x\n'


def test_the_header_of_a_tsv_is_its_first_line_even_if_a_cell_has_a_quote_in_it():
    text = 'id\tnote"\n1\tx\n2\ty\n'

    assert repeated_header(text, 8, "tsv") == 'id\tnote"'


def test_a_line_that_looks_like_a_sheet_marker_inside_a_quoted_cell_is_not_a_sheet():
    text = '## Sheet: Real\nid,note\n1,"notes\n## Sheet: Fake\ncontinued"\n2,after\n3,more\n'

    assert sheet_header(text, text.index("3,more")) == "## Sheet: Real\nid,note"
    assert sheet_header(text, text.index("2,after")) == "## Sheet: Real\nid,note"


def test_a_real_sheet_after_a_cell_that_mentions_a_marker_is_still_found():
    text = '## Sheet: One\nid,note\n1,"## Sheet: Fake\nstill the cell"\n## Sheet: Two\ncol,x\na,1\nb,2\n'

    assert sheet_header(text, text.index("b,2")) == "## Sheet: Two\ncol,x"

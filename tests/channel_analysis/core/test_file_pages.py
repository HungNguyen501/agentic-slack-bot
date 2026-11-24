from channel_analysis.core.file_pages import cut_page


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

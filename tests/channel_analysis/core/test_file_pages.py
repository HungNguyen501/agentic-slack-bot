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

import pytest

from agent.forms.access_request_options import FormOptions, FormOptionsError, allowed_value_groups, build_form_options

LOCATIONS = [
    {"company_key": "schwazze_com", "company": "Schwazze", "location_market": "CO", "location": "SB - Boulder", "location_id": "1"},
    {"company_key": "eaze_com", "company": "Eaze", "location_market": "CA", "location": "Eaze", "location_id": "2"},
    {"company_key": "schwazze_com", "company": "Schwazze", "location_market": "NM", "location": "RGO 1 Midtown", "location_id": "3"},
    {"company_key": "schwazze_com", "company": "Schwazze", "location_market": "CO", "location": "SB - Boulder", "location_id": "4"},
    {"company_key": "", "company": "", "location_market": None, "location": " ", "location_id": "5"},
]
TAGS = [{"tag_name": "transaction"}, {"tag_name": "arches_sales"}, {"tag_name": None}, {"tag_name": "transaction"}]


def test_options_are_grouped_cleaned_deduplicated_and_sorted():
    options = build_form_options(LOCATIONS, TAGS)

    assert options.locations_by_company == {"Eaze": ["Eaze"], "Schwazze": ["RGO 1 Midtown", "SB - Boulder"]}
    assert options.company_keys == ["eaze_com", "schwazze_com"]
    assert options.market_codes == ["CA", "CO", "NM"]
    assert options.tags == ["arches_sales", "transaction"]


def test_options_survive_a_json_round_trip():
    options = build_form_options(LOCATIONS, TAGS)

    assert FormOptions.from_json(options.to_json()) == options


@pytest.mark.parametrize(
    ("locations", "tags", "message"),
    [
        ([], TAGS, "store_locations"),
        (LOCATIONS, [], "row_filter_tags"),
        ([{"company": "Eaze"}], TAGS, "missing column"),
        (LOCATIONS, [{"name": "x"}], "tag_name"),
        ([{"company_key": "k", "company": "", "location_market": "CA", "location": "x"}], TAGS, "company and a location"),
    ],
)
def test_unusable_datasets_are_refused_with_a_message_naming_the_problem(locations, tags, message):
    with pytest.raises(FormOptionsError, match=message):
        build_form_options(locations, tags)


def test_a_dropdown_over_slacks_option_limit_is_refused():
    tags = [{"tag_name": f"tag_{i}"} for i in range(101)]

    with pytest.raises(FormOptionsError, match="at most 100"):
        build_form_options(LOCATIONS, tags)


@pytest.mark.parametrize("raw", ["not json", "{}", '{"locations_by_company": {}, "market_codes": [], "company_keys": []}', "[]"])
def test_a_snapshot_this_code_cant_read_raises_value_error(raw):
    with pytest.raises(ValueError, match="Malformed|Expecting"):
        FormOptions.from_json(raw)


def test_a_snapshot_with_an_extra_field_from_another_version_still_loads():
    options = build_form_options(LOCATIONS, TAGS)
    raw = options.to_json()[:-1] + ', "future_field": 1}'

    assert FormOptions.from_json(raw) == options


def location_rows(companies, per_company=1, market="CO"):
    return [
        {"company_key": f"c{c}_com", "company": f"Company {c}", "location_market": market, "location": f"Store {c}-{n}"}
        for c in range(companies)
        for n in range(per_company)
    ]


@pytest.mark.parametrize("column", ["company_key", "location_market"])
def test_a_column_that_is_blank_in_every_row_is_refused_instead_of_making_an_empty_dropdown(column):
    rows = [row | {column: " "} for row in LOCATIONS] + [row | {column: None} for row in LOCATIONS]

    with pytest.raises(FormOptionsError, match=f"no values in column {column}"):
        build_form_options(rows, TAGS)


@pytest.mark.parametrize(("length", "fits"), [(150, True), (151, False)])
def test_option_values_are_limited_to_slacks_150_characters_and_never_truncated(length, fits):
    long_value = "x" * length
    locations = [{"company_key": "k", "company": "Co", "location_market": "CO", "location": long_value}]
    tags = [{"tag_name": long_value}]

    if fits:
        options = build_form_options(locations, tags)
        assert options.locations_by_company["Co"] == [long_value] and options.tags == [long_value]
    else:
        with pytest.raises(FormOptionsError, match="151 characters.*at most 150"):
            build_form_options(locations, tags)


@pytest.mark.parametrize("column", ["location", "company_key", "location_market", "tag_name"])
def test_a_too_long_value_is_refused_in_every_dropdown(column):
    locations = [{"company_key": "k", "company": "Co", "location_market": "CO", "location": "l"}]
    tags = [{"tag_name": "t"}]
    target = tags if column == "tag_name" else locations
    target[0][column] = "x" * 151

    with pytest.raises(FormOptionsError, match="at most 150"):
        build_form_options(locations, tags)


@pytest.mark.parametrize(("companies", "fits"), [(98, True), (99, False)])
def test_the_fixed_groups_count_towards_slacks_limit_of_100_option_groups(companies, fits):
    # 98 companies + "Wildcard" + "Market codes" = 100 groups; one more is 101.
    rows = location_rows(companies)

    if fits:
        assert len(allowed_value_groups(build_form_options(rows, TAGS))) == 100
    else:
        with pytest.raises(FormOptionsError, match="101 option groups"):
            build_form_options(rows, TAGS)


def test_chunks_of_a_large_company_count_towards_the_group_limit():
    rows = location_rows(97) + [
        {"company_key": "big_com", "company": "Big", "location_market": "CO", "location": f"Big {n:03}"} for n in range(201)
    ]  # 98 companies, one of them split in 3 -> 100 + 2 chunks + 2 fixed groups

    with pytest.raises(FormOptionsError, match="option groups"):
        build_form_options(rows, TAGS)


def test_a_group_over_slacks_limit_is_split_into_numbered_parts():
    options = FormOptions(
        locations_by_company={"Big": [f"v{i}" for i in range(150)], "Small": ["a"]}, market_codes=["CA"], company_keys=["k"], tags=["t"]
    )

    groups = allowed_value_groups(options)

    assert [(label, len(values)) for label, values in groups] == [("Wildcard", 1), ("Big (1/2)", 100), ("Big (2/2)", 50), ("Small", 1), ("Market codes", 1)]


def test_chunked_labels_never_overwrite_a_real_company_with_the_same_name():
    options = FormOptions(
        locations_by_company={"Acme": [f"v{i}" for i in range(101)], "Acme (1/2)": ["real"]}, market_codes=["CA"], company_keys=["k"], tags=["t"]
    )

    groups = allowed_value_groups(options)

    assert [(label, len(values)) for label, values in groups if label == "Acme (1/2)"] == [("Acme (1/2)", 100), ("Acme (1/2)", 1)]
    assert sum(len(values) for _, values in groups) == 1 + 101 + 1 + 1


def test_long_company_names_sharing_a_prefix_keep_all_their_locations():
    prefix = "C" * 70
    options = FormOptions(
        locations_by_company={prefix + "1": [f"a{i}" for i in range(101)], prefix + "2": [f"b{i}" for i in range(101)]},
        market_codes=["CA"], company_keys=["k"], tags=["t"],
    )  # fmt: skip

    groups = allowed_value_groups(options)

    assert sum(len(values) for _, values in groups) == 1 + 202 + 1
    assert all(len(label) <= 75 for label, _ in groups)

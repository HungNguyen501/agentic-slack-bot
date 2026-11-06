import pytest

from agent.forms.access_request_options import FormOptions, FormOptionsError, build_form_options

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

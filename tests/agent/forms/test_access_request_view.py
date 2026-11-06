from agent.forms.access_request_options import FormOptions
from agent.forms.access_request_view import FormField, build_access_request_view, build_form_fields

OPTIONS = FormOptions(
    locations_by_company={"Eaze": ["Eaze"], "Schwazze": ["SB - Boulder"]},
    market_codes=["CA", "CO"],
    company_keys=["eaze_com", "schwazze_com"],
    tags=["arches_sales", "transaction"],
)


def block(view, block_id):
    return next(b for b in view["blocks"] if b["block_id"] == block_id)


def values(options):
    return [o["value"] for o in options]


def test_the_data_driven_dropdowns_are_built_from_the_options():
    view = build_access_request_view("r", "{}", OPTIONS)

    groups = block(view, "allowed_value")["element"]["option_groups"]
    assert [g["label"]["text"] for g in groups] == ["Wildcard", "Eaze", "Schwazze", "Market codes"]
    assert values(groups[3]["options"]) == ["CA", "CO"]
    assert values(block(view, "scope_value")["element"]["options"]) == ["eaze_com", "schwazze_com"]
    assert values(block(view, "tags")["element"]["options"]) == ["arches_sales", "transaction"]


def test_the_form_keeps_its_fixed_fields_in_order():
    assert [f.block_id for f in build_form_fields(OPTIONS)] == [
        "ticket_id", "user_emails", "filter_column", "allowed_value", "scope_column", "scope_value", "principal_type", "groups", "tags",
    ]  # fmt: skip


def test_a_group_over_slacks_limit_is_split_into_numbered_parts():
    field = FormField(block_id="x", label="X", groups={"Big": [f"v{i}" for i in range(150)], "Small": ["a"]})

    groups = field.to_block()["element"]["option_groups"]

    assert [(g["label"]["text"], len(g["options"])) for g in groups] == [("Big (1/2)", 100), ("Big (2/2)", 50), ("Small", 1)]


def test_long_option_text_is_cut_for_display_but_the_value_stays_whole():
    long_value = "x" * 120

    option = FormField(block_id="x", label="X", options=[long_value]).to_block()["element"]["options"][0]

    assert option["value"] == long_value
    assert len(option["text"]["text"]) == 75

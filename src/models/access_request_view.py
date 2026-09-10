"""Data model for the data access request form and its Slack modal view."""
import re
from dataclasses import dataclass, field

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class FormField:
    """One input field of the data access request modal.

    Args:
        block_id: Slack block_id — also the key the submitted value is stored under.
        label: Field label shown to the user.
        options: Selectable values; ignored when text_input is True.
        multi: Render as a multi_static_select instead of a static_select.
        text_input: Render as a free-text plain_text_input instead of a select.
    """

    block_id: str
    label: str
    options: list[str] = field(default_factory=list)
    multi: bool = False
    text_input: bool = False

    def to_block(self) -> dict:
        """Render this field as a Slack Block Kit input block."""
        if self.text_input:
            element = {"type": "plain_text_input", "action_id": "value"}
        else:
            element = {
                "type": "multi_static_select" if self.multi else "static_select",
                "action_id": "value",
                "options": [{"text": {"type": "plain_text", "text": o}, "value": o} for o in self.options],
            }
        return {
            "type": "input",
            "block_id": self.block_id,
            "label": {"type": "plain_text", "text": self.label},
            "element": element,
        }


ACCESS_REQUEST_FORM_FIELDS: list[FormField] = [
    FormField(block_id="user_email", label="User email", text_input=True),
    FormField(block_id="filter_column", label="Filter column", options=["location", "location_market"]),
    FormField(
        block_id="allowed_value",
        label="Allowed value",
        options=[
            "ALL",
            "Blaine",
            "Blaine Rec",
            "Bloomington",
            "Bloomington Rec",
            "Burnsville",
            "Burnsville Rec",
            "CO",
            "Dundalk",
            "Eastern",
            "Frederick",
            "Henderson",
            "Hermantown",
            "Hermantown Rec",
            "Kirkwood",
            "MD",
            "MI",
            "MN",
            "MO",
            "Minneapolis",
            "Minneapolis Rec",
            "Moorhead",
            "Moorhead Rec",
            "NM",
            "NV",
            "NY",
            "Rochester",
            "Sahara",
            "Saint Louis",
            "West Wendover",
            "Woodbury",
            "Woodbury Rec",
        ],
    ),
    FormField(block_id="scope_column", label="Scope column", options=["company_key"]),
    FormField(
        block_id="scope_value",
        label="Scope value",
        options=[
            "commoncitizen_com",
            "deeproots_com",
            "eaze_com",
            "fluent_com",
            "frx_com",
            "propermo_com",
            "schwazze_com",
            "vireohealth_com",
            "wholesomeco_com",
        ],
    ),
    FormField(block_id="principal_type", label="Principal type", options=["Service principals",]),
    FormField(
        block_id="groups",
        label="Groups",
        options=[
            "gpt-schwazze-users",
            "gpt-users",
        ],
        multi=True,
    ),
    FormField(
        block_id="tags",
        label="Tags",
        options=[
            "b2b_orders_items",
            "current_inventory",
            "customer_lifetime_value",
            "fast_score",
            "fresh_score",
            "full_score",
            "inventory_snapshot",
            "inventory_transaction",
            "store_location",
            "transaction",
            "variant_semantic",
            "wurk",
        ],
        multi=True,
    ),
]


def build_access_request_view(request_type: str, private_metadata: str) -> dict:
    """Build the data access request modal view. Pure function, no I/O.

    Args:
        request_type: The access request category (currently unused in the view itself,
            kept for when multiple request types need distinct forms).
        private_metadata: Opaque JSON string (channel/thread_ts/requester_id/reviewers) round-tripped
            unchanged by Slack and read back from view_submission.

    Returns:
        A Slack view payload suitable for views.open.
    """
    return {
        "type": "modal",
        "callback_id": "access_request_form",
        "private_metadata": private_metadata,
        "title": {"type": "plain_text", "text": "Row Filter Access"},
        "submit": {"type": "plain_text", "text": "Submit"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [f.to_block() for f in ACCESS_REQUEST_FORM_FIELDS],
    }


def validate_submission(fields: dict) -> dict[str, str]:
    """Validate submitted form values. Pure function, no I/O.

    Args:
        fields: Flattened {block_id: value} dict extracted from the modal's view.state.values.

    Returns:
        Empty dict if the submission is valid; otherwise a {block_id: error message} mapping
        in the shape Slack expects for a view_submission response_action of "errors".
    """
    errors = {}
    user_email = (fields.get("user_email") or "").strip()

    if not EMAIL_PATTERN.match(user_email):
        errors["user_email"] = "Enter a valid email address, e.g. name@example.com."

    return errors

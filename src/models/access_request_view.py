"""Data model for the data access request form and its Slack modal view."""
from dataclasses import dataclass, field


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
    FormField(block_id="filter_column", label="Filter column", options=["location_market", "company_key"]),
    FormField(block_id="allowed_value", label="Allowed value", options=["MN", "vireohealth_com"]),
    FormField(block_id="scope_column", label="Scope column", options=["company_key", "location_market"]),
    FormField(block_id="scope_value", label="Scope value", options=["vireohealth_com", "MN"]),
    FormField(block_id="principal_type", label="Principal type", options=["Service principals", "Users"]),
    FormField(block_id="groups", label="Groups", options=["gpt-users"], multi=True),
    FormField(
        block_id="tags",
        label="Tags",
        options=[
            "transaction",
            "current_inventory",
            "fast_score",
            "fresh_score",
            "full_score",
            "customer_lifetime_value",
            "b2b_orders_items",
            "store_location",
            "inventory_snapshot",
        ],
        multi=True,
    ),
]

# Slack modal titles are capped at 24 characters; "Row Filter Access Request" is 25.
MODAL_TITLE = "Row Filter Access"


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
        "title": {"type": "plain_text", "text": MODAL_TITLE},
        "submit": {"type": "plain_text", "text": "Submit"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [f.to_block() for f in ACCESS_REQUEST_FORM_FIELDS],
    }


def format_submission_table(fields: dict) -> str:
    """Render submitted form values as a Slack mrkdwn code-block table, in field-definition order.

    Args:
        fields: Flattened {block_id: value} dict from a view_submission (values are strings,
            or lists of strings for multi-select fields).

    Returns:
        A fenced code block with a two-column "Field  Value" table.
    """
    rows = []
    for form_field in ACCESS_REQUEST_FORM_FIELDS:
        value = fields.get(form_field.block_id)
        if isinstance(value, list):
            value = ", ".join(value)
        rows.append((form_field.label, value or "—"))

    label_width = max(len(label) for label, _ in rows)
    lines = [f"{label:<{label_width}}  {value}" for label, value in rows]
    return "```\n" + "\n".join(lines) + "\n```"

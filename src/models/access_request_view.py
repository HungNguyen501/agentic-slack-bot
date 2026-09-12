"""Data model for the data access request form and its Slack modal view."""
import re
from dataclasses import dataclass, field

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# How long the "Open Form" button posted by _dispatch_data_access_tool stays clickable.
# src/worker/agent.py uses this to stamp expires_at into the button's value when posting it;
# src/receiver/app.py just compares expires_at against the current time, no TTL math of its own.
ACCESS_REQUEST_BUTTON_TTL_SECONDS = 3600


def parse_emails(raw: str) -> list[str]:
    """Split a free-text user_emails field into a deduped list of individual emails.

    Args:
        raw: The submitted user_emails value — one email per line. Commas are not
            treated as separators; a comma on a line makes that line invalid (caught
            by validate_submission), not multiple emails.

    Returns:
        Stripped, non-empty emails in first-seen order, deduplicated case-insensitively
        (a repeated email typed twice shouldn't produce two separate requests).
    """
    seen = set()
    emails = []
    for line in (raw or "").split("\n"):
        email = line.strip()
        if email and email.lower() not in seen:
            seen.add(email.lower())
            emails.append(email)
    return emails


@dataclass(frozen=True)
class FormField:
    """One input field of the data access request modal.

    Args:
        block_id: Slack block_id — also the key the submitted value is stored under.
        label: Field label shown to the user.
        options: Selectable values; ignored when text_input is True.
        multi: Render as a multi_static_select instead of a static_select.
        text_input: Render as a free-text plain_text_input instead of a select.
        multiline: For a text_input field, render a taller box that accepts newlines
            (used by user_emails, which requires exactly one email per line).
    """

    block_id: str
    label: str
    options: list[str] = field(default_factory=list)
    multi: bool = False
    text_input: bool = False
    multiline: bool = False

    def to_block(self) -> dict:
        """Render this field as a Slack Block Kit input block."""
        if self.text_input:
            element: dict = {"type": "plain_text_input", "action_id": "value"}
            if self.multiline:
                element["multiline"] = True
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
    FormField(block_id="ticket_id", label="Ticket ID", text_input=True),
    FormField(block_id="user_emails", label="User email(s) — one per line", text_input=True, multiline=True),
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
    raw = fields.get("user_emails") or ""

    comma_lines = [line.strip() for line in raw.split("\n") if "," in line]
    if comma_lines:
        errors["user_emails"] = "Enter one email per line — commas are not allowed, e.g. remove the comma(s) in: " + ", ".join(comma_lines)
        return errors

    emails = parse_emails(raw)

    if not emails:
        errors["user_emails"] = "Enter at least one valid email address, e.g. name@example.com."
    else:
        invalid = [e for e in emails if not EMAIL_PATTERN.match(e)]
        if invalid:
            errors["user_emails"] = f"Invalid email address(es): {', '.join(invalid)}"

    return errors

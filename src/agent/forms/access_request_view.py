"""Data model for the data access request form and its Slack modal view."""
import re
from dataclasses import dataclass, field

from agent.forms.access_request_options import FormOptions

# Slack's limits on select menus: 75 characters of option text, and 100 options per select / per option group.
_OPTION_TEXT_MAX = 75
_GROUP_OPTIONS_MAX = 100

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


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


def _option(value: str) -> dict:
    """A Slack option for `value`; long text is cut for display, the submitted value stays whole."""
    text = value if len(value) <= _OPTION_TEXT_MAX else value[: _OPTION_TEXT_MAX - 1] + "…"
    return {"text": {"type": "plain_text", "text": text}, "value": value}


def _chunk_groups(groups: dict[str, list[str]]) -> dict[str, list[str]]:
    """Split any option group over Slack's per-group limit into numbered parts, e.g. "Schwazze (1/2)"."""
    chunked: dict[str, list[str]] = {}
    for label, values in groups.items():
        parts = [values[i:i + _GROUP_OPTIONS_MAX] for i in range(0, len(values), _GROUP_OPTIONS_MAX)]
        if len(parts) <= 1:
            chunked[label] = values
            continue
        for number, part in enumerate(parts, start=1):
            chunked[f"{label[: _OPTION_TEXT_MAX - 8]} ({number}/{len(parts)})"] = part
    return chunked


@dataclass(frozen=True)
class FormField:
    """One input field of the data access request modal.

    Args:
        block_id: Slack block_id — also the key the submitted value is stored under.
        label: Field label shown to the user.
        options: Selectable values; ignored when text_input is True or groups is set.
        groups: Selectable values grouped under option_groups (Slack's static_select caps
            flat options at 100 — allowed_value alone spans 200+ locations across companies,
            so it's grouped by company instead). Ignored when text_input is True.
        multi: Render as a multi_static_select instead of a static_select.
        text_input: Render as a free-text plain_text_input instead of a select.
        multiline: For a text_input field, render a taller box that accepts newlines
            (used by user_emails, which requires exactly one email per line).
        default: Values pre-selected when the modal opens; must be a subset of options.
            Ignored when text_input is True or groups is set.
    """

    block_id: str
    label: str
    options: list[str] = field(default_factory=list)
    groups: dict[str, list[str]] | None = None
    multi: bool = False
    text_input: bool = False
    multiline: bool = False
    default: list[str] = field(default_factory=list)

    def to_block(self) -> dict:
        """Render this field as a Slack Block Kit input block."""
        if self.text_input:
            element: dict = {"type": "plain_text_input", "action_id": "value"}
            if self.multiline:
                element["multiline"] = True
        else:
            element = {"type": "multi_static_select" if self.multi else "static_select", "action_id": "value"}
            if self.groups:
                element["option_groups"] = [
                    {"label": {"type": "plain_text", "text": group_label[:_OPTION_TEXT_MAX]}, "options": [_option(o) for o in group_options]}
                    for group_label, group_options in _chunk_groups(self.groups).items()
                ]
            else:
                element["options"] = [_option(o) for o in self.options]
                initial = [_option(o) for o in self.default]
                if initial:
                    if self.multi:
                        element["initial_options"] = initial
                    else:
                        element["initial_option"] = initial[0]
        return {"type": "input", "block_id": self.block_id, "label": {"type": "plain_text", "text": self.label}, "element": element}


def build_form_fields(options: FormOptions) -> list[FormField]:
    """The modal's fields. The location, company and tag dropdowns come from `options` (synced data); the rest is fixed."""
    return [
        FormField(block_id="ticket_id", label="Ticket ID", text_input=True),
        FormField(block_id="user_emails", label="User email(s) — one per line", text_input=True, multiline=True),
        FormField(block_id="filter_column", label="Filter column", options=["location", "location_market"]),
        FormField(
            block_id="allowed_value",
            label="Allowed value",
            groups={"Wildcard": ["ALL"], **options.locations_by_company, "Market codes": options.market_codes},
        ),
        FormField(block_id="scope_column", label="Scope column", options=["company_key"]),
        FormField(block_id="scope_value", label="Scope value", options=options.company_keys),
        FormField(block_id="principal_type", label="Principal type", options=["Service principals"]),
        FormField(
            block_id="groups",
            label="Groups (Rocky Mountain gpt users? Also select gpt-schwazze-users)",
            options=["gpt-schwazze-users", "gpt-users"],
            multi=True,
            default=["gpt-users"],
        ),
        FormField(block_id="tags", label="Tags", options=options.tags, multi=True),
    ]


def build_access_request_view(request_type: str, private_metadata: str, options: FormOptions) -> dict:
    """Build the data access request modal view. Pure function, no I/O.

    Args:
        request_type: The access request category (currently unused in the view itself,
            kept for when multiple request types need distinct forms).
        private_metadata: Opaque JSON string (channel/thread_ts/requester_id/reviewers) round-tripped
            unchanged by Slack and read back from view_submission.
        options: The synced data behind the location, company and tag dropdowns.

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
        "blocks": [f.to_block() for f in build_form_fields(options)],
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

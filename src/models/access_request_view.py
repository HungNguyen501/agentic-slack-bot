"""Data model for the data access request form and its Slack modal view."""
import re
from dataclasses import dataclass, field

_LOCATIONS_BY_COMPANY: dict[str, list[str]] = {
    "Agri-Genesis": ["Clinton", "Florissant", "Kansas City", "Maryville", "Sunrise Beach"],
    "Common Citizen": ["Cannavista", "Detroit", "Ferndale", "Fuel 420", "Grand Rapids", "Lake Orion", "Lansing", "LIV Pontiac REC", "Pure Lapeer", "Westland", "Xplore"],
    "Deep Roots": [
        "Blue Diamond",
        "Cheyenne",
        "Deer Springs",
        "Eastern",
        "Fernley",
        "Henderson",
        "Homestead",
        "Mesquite",
        "North Las Vegas",
        "Sahara",
        "Sparks",
        "Virginia",
        "Water St",
        "West Wendover",
    ],
    "Eaze": ["Eaze"],
    "Fluent": ["FLUENT Chestertown", "FLUENT Kingston", "FLUENT Manhattan", "FLUENT White Plains"],
    "FRX": ["Cuyahoga Falls", "East Liverpool", "Elyria", "Harrison", "Hubbard", "Keowee", "New Paris", "Norwood", "Third Street", "Verity", "Waterloo"],
    "Proper MO": [
        "Bridgeton",
        "Cape Girardeau - Elevate",
        "Cape Girardeau - Proper",
        "Crestwood",
        "Ellisville",
        "Festus East",
        "Festus West",
        "House Springs",
        "Kirkwood",
        "North KC",
        "Saint Louis",
        "University City",
        "Warrenton",
    ],
    "Schwazze": [
        "EDW - 6th Ave (Aurora)",
        "EDW - Aurora",
        "EDW - Breckenridge",
        "EDW - Brighton",
        "EDW - Capitol Hill (Grant)",
        "EDW - Central Boulder",
        "EDW - Central Denver (Byers Place)",
        "EDW - Colfax (East Denver)",
        "EDW - Commerce City",
        "EDW - DU (Dispensary)",
        "EDW - Federal Heights",
        "EDW - Fort Collins - College Ave",
        "EDW - Fort Collins - Smithfield",
        "EDW - Mile High",
        "EDW - NW Denver/Pecos",
        "EDW - Sheridan (West Denver)",
        "EDW02 Sunland Park",
        "EF - South Boulder",
        "EV02 Far NE Heights",
        "EV03 West Central",
        "EV04 Los Lunas",
        "EV05 Uptown",
        "EV07 Paradise Hills",
        "EV09 Montano Plaza",
        "EV10 Sunland Park",
        "EV15 Las Cruces North",
        "Green Dragon - Aspen",
        "Green Dragon - Edgewater",
        "Green Dragon - Telluride",
        "INVENTORY WAREHOUSE",
        "LW - Berthoud",
        "LW - Broomfield",
        "LW - Buckley",
        "LW - Central Park",
        "LW - Colorado Blvd",
        "LW - Cortez",
        "LW - DTC",
        "LW - Federal Heights",
        "LW - Fort Collins",
        "LW - Garden City",
        "LW - Lakewood",
        "LW - Log Lane",
        "LW - Peoria",
        "LW - Pueblo North",
        "LW - RiNo",
        "LW - Tower Road",
        "LW - Uptown",
        "Medicine Man - Aurora",
        "Medicine Man - Longmont",
        "Medicine Man - Thornton",
        "RGO 1 Midtown",
        "RGO 2 West Side",
        "RGO 3 Grants",
        "RGO 4 Cottonwood",
        "RGO 6 Northeast Heights",
        "RGO 7 Roswell",
        "RGO 8 Las Cruces",
        "RGO 9 Las Vegas",
        "RGO10 Santa Fe",
        "RGO11 Yale",
        "RGO13 Clovis",
        "RGO18 Carlsbad",
        "RGO19 Hobbs",
        "SA - Denver",
        "SB - Arapahoe",
        "SB - Belmar",
        "SB - Boulder",
        "SB - Colorado Springs",
        "SB - Fort Collins",
        "SB - Garden City",
        "SB - Glendale",
        "SB - Highlands",
        "SB - Lakeside",
        "SB - Longmont",
        "SB - Louisville",
        "SB - Manitou Springs",
        "SB - Niwot",
        "SB - North Denver",
        "SB - Pueblo",
        "SB - Pueblo West",
        "SB - Quincy (Aurora)",
        "SB - Thornton",
        "TGS - 20th",
        "TGS - Fort Collins",
        "TGS - Highway 6",
        "TGS - Northglenn",
        "TGS - Potomac",
        "TGS - Quincy",
        "TGS - Sheridan",
        "TGS - South Aurora",
        "TGS - Westminster",
        "TGS - Wewatta",
    ],
    "Silverpeak": ["Alameda", "Aspen", "Courtesy", "Dillon", "Downtown", "Glenwood", "Parachute", "XX Distribution Center"],
    "The-Cannabist": [
        "Cannabist Chicago (Dispensary - MED/ADULT USE)",
        "Cannabist Lowell (Dispensary - ADULT USE)",
        "Cannabist Lowell (Dispensary - Medical)",
        "Cannabist Villa Park (Dispensary - ADULT USE)",
        "Medicine Man 84th @ Thornton (Dispensary - ADULT USE)",
        "Medicine Man Havana @ Aurora (Dispensary - ADULT USE)",
        "Medicine Man Nome St @ Denver (Dispensary - ADULT USE)",
        "Medicine Man Nome St @ Denver (Dispensary - Medical)",
        "Medicine Man Rogers @ Longmont (Dispensary - ADULT USE)",
        "Patriot Care Greenfield (Dispensary - ADULT USE)",
        "Patriot Care Greenfield (Dispensary - Medical)",
        "TGS 20th Ave @ Edgewater (Dispensary - ADULT USE)",
        "TGS Colfax Ave @ East Aurora (Dispensary - ADULT USE)",
        "TGS College Avenue @ Fort Collins (Dispensary - ADULT USE)",
        "TGS College Avenue @ Fort Collins (Dispensary - Medical)",
        "TGS E Montview Blvd @ Aurora (Dispensary - ADULT USE)",
        "TGS Federal Blvd @ Westminster (Dispensary - ADULT USE)",
        "TGS Grape St @ North Denver (Dispensary - ADULT USE)",
        "TGS Hwy 6 & 24 @ Glenwood Springs (Dispensary - ADULT USE)",
        "TGS Kentucky Ave @ Glendale (Dispensary - ADULT USE)",
        "TGS Malley Dr @ Northglenn (Dispensary - ADULT USE)",
        "TGS Peoria Ct @ South Aurora (Dispensary - ADULT USE)",
        "TGS Potomac @ Central Aurora (Dispensary - ADULT USE)",
        "TGS Quincy Ave @ Southeast Aurora (Dispensary - ADULT USE)",
        "TGS S Federal Blvd @ Sheridan (Dispensary - ADULT USE)",
        "TGS Southgate Pl @ Pueblo (Dispensary - ADULT USE)",
        "TGS Steele St @ Denver (Cultivation - Adult Use)",
        "TGS Washington St @ Denver (Manufacturing - ADULT USE)",
        "TGS Wewatta St @ Union Station (Dispensary - ADULT USE)",
    ],
    "Vireo Health": [
        "Albany",
        "Blaine",
        "Bloomington",
        "Burnsville",
        "Dundalk",
        "Elmhurst",
        "Frederick",
        "Hampden",
        "Hermantown",
        "Johnson City",
        "Minneapolis",
        "Moorhead",
        "Rochester",
        "Rockville",
        "White Plains",
        "Woodbury",
    ],
    "WholesomeCo": ["Wholesome"],
}

_MARKET_CODES: list[str] = ["CA", "CO", "FL", "IL", "MA", "MD", "MI", "MN", "MO", "NM", "NV", "NY", "OH", "UT"]

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
                    {"label": {"type": "plain_text", "text": group_label}, "options": [{"text": {"type": "plain_text", "text": o}, "value": o} for o in group_options]}
                    for group_label, group_options in self.groups.items()
                ]
            else:
                element["options"] = [{"text": {"type": "plain_text", "text": o}, "value": o} for o in self.options]
                initial = [{"text": {"type": "plain_text", "text": o}, "value": o} for o in self.default]
                if initial:
                    if self.multi:
                        element["initial_options"] = initial
                    else:
                        element["initial_option"] = initial[0]
        return {"type": "input", "block_id": self.block_id, "label": {"type": "plain_text", "text": self.label}, "element": element}


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


ACCESS_REQUEST_FORM_FIELDS: list[FormField] = [
    FormField(block_id="ticket_id", label="Ticket ID", text_input=True),
    FormField(block_id="user_emails", label="User email(s) — one per line", text_input=True, multiline=True),
    FormField(block_id="filter_column", label="Filter column", options=["location", "location_market"]),
    FormField(block_id="allowed_value", label="Allowed value", groups={"Wildcard": ["ALL"], **_LOCATIONS_BY_COMPANY, "Market codes": _MARKET_CODES}),
    FormField(block_id="scope_column", label="Scope column", options=["company_key"]),
    FormField(
        block_id="scope_value",
        label="Scope value",
        options=[
            "agri_genesis_com",
            "commoncitizen_com",
            "deeproots_com",
            "eaze_com",
            "fluent_com",
            "frx_com",
            "propermo_com",
            "schwazze_com",
            "silverpeak_com",
            "thecannabist_com",
            "vireohealth_com",
            "wholesomeco_com",
        ],
    ),
    FormField(block_id="principal_type", label="Principal type", options=["Service principals"]),
    FormField(
        block_id="groups",
        label="Groups (Rocky Mountain gpt users? Also select gpt-schwazze-users)",
        options=["gpt-schwazze-users", "gpt-users"],
        multi=True,
        default=["gpt-users"],
    ),
    FormField(
        block_id="tags",
        label="Tags",
        options=[
            "arches_sales",
            "b2b_orders_items",
            "current_inventory",
            "customer_lifetime_value",
            "fast_score",
            "fresh_score",
            "friendly_score",
            "full_score",
            "inventory_snapshot",
            "inventory_transaction",
            "promotion_calendar",
            "store_location",
            "transaction",
            "variant_semantic",
        ],
        multi=True,
    ),
]

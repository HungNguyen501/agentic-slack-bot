"""The synced reference data the data access request form's dropdowns are built from. Pure, no I/O."""
import json
from dataclasses import asdict, dataclass

# Names of the synced_datasets (see agent/dataset_sync/) the form reads.
LOCATIONS_DATASET = "store_locations"
TAGS_DATASET = "row_filter_tags"


def options_redis_key(token: str) -> str:
    """Redis key holding a FormOptions snapshot for one posted "Open Form" button (written by the worker, read by the receiver)."""
    return f"access_form_options:{token}"


# Slack's limits on select menus: 100 options per select and per option group, 100 option groups, 150 characters in an
# option's value (the part submitted, which identifies the access requested, so it is never shortened) and 75 in a group label.
_SLACK_MAX_OPTIONS = 100
_SLACK_MAX_GROUPS = 100
_SLACK_MAX_VALUE_LENGTH = 150
_SLACK_MAX_LABEL_LENGTH = 75


class FormOptionsError(ValueError):
    """The synced data can't make a working form; the message says what to fix and is fit to show an admin."""


@dataclass(frozen=True)
class FormOptions:
    """What the form's data-driven dropdowns offer.

    `locations_by_company` maps a company's display name to its location names (the "allowed value" choices);
    `market_codes` are the location market codes (also "allowed value" choices); `company_keys` are the "scope value" choices.
    """

    locations_by_company: dict[str, list[str]]
    market_codes: list[str]
    company_keys: list[str]
    tags: list[str]

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str | bytes) -> "FormOptions":
        """Rebuild options from a snapshot read back from Redis.

        Raises:
            ValueError: the snapshot isn't valid JSON or lacks a field, e.g. one written by a different version of the code.
        """
        try:
            data = json.loads(raw)
            return cls(
                locations_by_company=data["locations_by_company"],
                market_codes=data["market_codes"],
                company_keys=data["company_keys"],
                tags=data["tags"],
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"Malformed form options snapshot: {exc!r}") from exc


def allowed_value_groups(options: FormOptions) -> list[tuple[str, list[str]]]:
    """The "allowed value" dropdown's option groups, in order: the wildcard, each company's locations, then the market codes.

    A group over Slack's per-group limit is split into numbered parts ("Schwazze (1/2)"). A list rather than a mapping,
    so two groups whose labels happen to match can never overwrite each other.
    """
    groups: list[tuple[str, list[str]]] = [("Wildcard", ["ALL"]), *options.locations_by_company.items(), ("Market codes", options.market_codes)]
    split: list[tuple[str, list[str]]] = []
    for label, values in groups:
        parts = [values[i:i + _SLACK_MAX_OPTIONS] for i in range(0, len(values), _SLACK_MAX_OPTIONS)]
        if len(parts) <= 1:
            split.append((label[:_SLACK_MAX_LABEL_LENGTH], values))
            continue
        suffix_room = _SLACK_MAX_LABEL_LENGTH - len(f" ({len(parts)}/{len(parts)})")
        split.extend((f"{label[:suffix_room]} ({number}/{len(parts)})", part) for number, part in enumerate(parts, start=1))
    return split


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _distinct(rows: list[dict], column: str) -> list[str]:
    """The non-blank values of `column`, de-duplicated and sorted case-insensitively."""
    values = {_clean(row[column]) for row in rows} - {""}
    return sorted(values, key=lambda value: (value.lower(), value))


def _locations_by_company(rows: list[dict]) -> dict[str, list[str]]:
    """Each company's distinct locations, sorted, with the companies sorted too; rows lacking either are skipped."""
    locations: dict[str, list[dict]] = {}
    for row in rows:
        company, location = _clean(row["company"]), _clean(row["location"])
        if company and location:
            locations.setdefault(company, []).append({"location": location})
    return {company: _distinct(entries, "location") for company, entries in sorted(locations.items(), key=lambda item: item[0].lower())}


def _require_columns(dataset: str, rows: list[dict], columns: tuple[str, ...]) -> None:
    missing = [column for column in columns if column not in rows[0]]
    if missing:
        raise FormOptionsError(f"Dataset '{dataset}' is missing column(s) {', '.join(missing)}; check its query.")


def _check_value_lengths(label: str, values: list[str]) -> None:
    too_long = [value for value in values if len(value) > _SLACK_MAX_VALUE_LENGTH]
    if too_long:
        raise FormOptionsError(
            f"The form's {label} has a value of {len(too_long[0])} characters, but Slack allows at most {_SLACK_MAX_VALUE_LENGTH}: "
            f"'{too_long[0][:60]}...'. Shorten it in the source data."
        )


def build_form_options(location_rows: list[dict], tag_rows: list[dict]) -> FormOptions:
    """Turn the rows of the locations dataset (company_key, company, location_market, location) and the tags dataset (tag_name) into form options.

    Blank values are skipped and each list is de-duplicated and sorted, so the order of the query doesn't matter.

    Raises:
        FormOptionsError: a dataset is empty or lacks a column or a usable value, or the result can't fit Slack's limits
            (options per dropdown, option groups, value length) so that Slack would reject the whole form.
    """
    if not location_rows:
        raise FormOptionsError(f"Dataset '{LOCATIONS_DATASET}' has no rows.")
    if not tag_rows:
        raise FormOptionsError(f"Dataset '{TAGS_DATASET}' has no rows.")
    _require_columns(LOCATIONS_DATASET, location_rows, ("company_key", "company", "location_market", "location"))
    _require_columns(TAGS_DATASET, tag_rows, ("tag_name",))

    options = FormOptions(
        locations_by_company=_locations_by_company(location_rows),
        market_codes=_distinct(location_rows, "location_market"),
        company_keys=_distinct(location_rows, "company_key"),
        tags=_distinct(tag_rows, "tag_name"),
    )

    if not options.locations_by_company:
        raise FormOptionsError(f"Dataset '{LOCATIONS_DATASET}' has no rows with both a company and a location.")
    for column, values in (("company_key", options.company_keys), ("location_market", options.market_codes)):
        if not values:
            raise FormOptionsError(f"Dataset '{LOCATIONS_DATASET}' has no values in column {column}, which the form needs.")
    if not options.tags:
        raise FormOptionsError(f"Dataset '{TAGS_DATASET}' has no tag names.")

    for label, values in (("scope value (company keys)", options.company_keys), ("tags", options.tags)):
        if len(values) > _SLACK_MAX_OPTIONS:
            raise FormOptionsError(f"The form's {label} dropdown would have {len(values)} options, but Slack allows at most {_SLACK_MAX_OPTIONS}.")
    groups = allowed_value_groups(options)
    if len(groups) > _SLACK_MAX_GROUPS:
        raise FormOptionsError(f"The form's allowed value dropdown would have {len(groups)} option groups, but Slack allows at most {_SLACK_MAX_GROUPS}.")

    _check_value_lengths("allowed value dropdown", [value for _, values in groups for value in values])
    _check_value_lengths("scope value dropdown", options.company_keys)
    _check_value_lengths("tags dropdown", options.tags)
    return options

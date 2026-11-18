"""Seed files: Markdown with YAML frontmatter, parsed into a `Character`."""
import yaml

from characters.core.errors import InvalidSeedError
from characters.core.models import Character

_FIELDS = {"name", "display_name", "song", "artist", "enabled", "icon_emoji", "icon_url", "bindings"}
_FENCE = "---"


def _split_frontmatter(filename: str, text: str) -> tuple[str, str]:
    """The frontmatter and the body of a file. A fence is a line that is only `---`, so a YAML value that merely ends in
    or contains `---` (`song: hello---`) is not mistaken for one."""
    lines = text.replace("\r\n", "\n").split("\n")
    closing = next((i for i in range(1, len(lines)) if lines[i].rstrip() == _FENCE), None)
    if lines[0].rstrip() != _FENCE or closing is None:
        raise InvalidSeedError([f"{filename}: the file must start with a --- frontmatter block"])
    body_start = closing + 1
    return "\n".join(lines[1:closing]), "\n".join(lines[body_start:])


def _load_fields(filename: str, frontmatter: str) -> dict:
    try:
        data = yaml.safe_load(frontmatter)
    except yaml.YAMLError as exc:
        raise InvalidSeedError([f"{filename}: the frontmatter is not valid YAML ({exc})"]) from exc
    if not isinstance(data, dict):
        raise InvalidSeedError([f"{filename}: the frontmatter must be a set of fields"])
    return data


def _field_problems(filename: str, data: dict) -> list[str]:
    """What is wrong with the shape of the fields. The rules about their values are `characters.core.validation`'s."""
    problems = []
    if not isinstance(data.get("name"), str) or not data["name"]:
        problems.append("name is required")
    bindings = data.get("bindings", [])
    if not isinstance(bindings, list) or not all(isinstance(b, str) for b in bindings):
        problems.append("bindings must be a list of texts")
    if not isinstance(data.get("enabled", True), bool):
        problems.append("enabled must be true or false")
    problems += [
        f"{key} must be a text"
        for key in ("display_name", "song", "artist", "icon_emoji", "icon_url")
        if data.get(key) is not None and not isinstance(data[key], str)
    ]
    problems += [f"{key} must be a voice block (a set of fields)" for key, value in data.items() if key not in _FIELDS and not isinstance(value, dict)]
    return [f"{filename}: {problem}" for problem in problems]


def parse_character_file(filename: str, text: str) -> Character:
    """Parse the text of one seed file. The frontmatter holds the fields, the body is the persona, and every other
    top-level key is the voice block of the feature it names.

    Raises:
        InvalidSeedError: the file has no frontmatter, malformed YAML, or a field of the wrong type.
    """
    frontmatter, body = _split_frontmatter(filename, text)
    data = _load_fields(filename, frontmatter)
    problems = _field_problems(filename, data)
    if problems:
        raise InvalidSeedError(problems)

    return Character(
        name=data["name"],
        display_name=data.get("display_name") or data["name"],
        bindings=tuple(data.get("bindings", [])),
        persona=body.strip(),
        voices={key: value for key, value in data.items() if key not in _FIELDS},
        song=data.get("song") or "",
        artist=data.get("artist") or "",
        enabled=data.get("enabled", True),
        icon_emoji=data.get("icon_emoji"),
        icon_url=data.get("icon_url"),
    )

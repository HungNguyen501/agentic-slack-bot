"""Seed files: Markdown with YAML frontmatter, parsed into a `Character`."""
import yaml

from characters.core.errors import InvalidSeedError
from characters.core.models import Character

_FIELDS = {"name", "display_name", "song", "artist", "enabled", "icon_emoji", "icon_url", "bindings"}


def parse_character_file(filename: str, text: str) -> Character:
    """Parse the text of one seed file. The frontmatter holds the fields, the body is the persona, and every other
    top-level key is the voice block of the feature it names.

    Raises:
        InvalidSeedError: the file has no frontmatter, malformed YAML, or a field of the wrong type.
    """
    parts = text.replace("\r\n", "\n").split("---\n", 2)
    if len(parts) != 3 or parts[0].strip():
        raise InvalidSeedError([f"{filename}: the file must start with a --- frontmatter block"])
    try:
        data = yaml.safe_load(parts[1])
    except yaml.YAMLError as exc:
        raise InvalidSeedError([f"{filename}: the frontmatter is not valid YAML ({exc})"]) from exc
    if not isinstance(data, dict):
        raise InvalidSeedError([f"{filename}: the frontmatter must be a set of fields"])

    problems = []
    name = data.get("name")
    if not isinstance(name, str) or not name:
        problems.append(f"{filename}: name is required")
    bindings = data.get("bindings", [])
    if not isinstance(bindings, list) or not all(isinstance(b, str) for b in bindings):
        problems.append(f"{filename}: bindings must be a list of texts")
    enabled = data.get("enabled", True)
    if not isinstance(enabled, bool):
        problems.append(f"{filename}: enabled must be true or false")
    texts = {key: data.get(key) for key in ("display_name", "song", "artist", "icon_emoji", "icon_url")}
    problems += [f"{filename}: {key} must be a text" for key, value in texts.items() if value is not None and not isinstance(value, str)]
    voices = {key: value for key, value in data.items() if key not in _FIELDS}
    problems += [f"{filename}: {key} must be a voice block (a set of fields)" for key, value in voices.items() if not isinstance(value, dict)]
    if problems:
        raise InvalidSeedError(problems)

    return Character(
        name=name,
        display_name=texts["display_name"] or name,
        bindings=tuple(bindings),
        persona=parts[2].strip(),
        voices=voices,
        song=texts["song"] or "",
        artist=texts["artist"] or "",
        enabled=enabled,
        icon_emoji=texts["icon_emoji"],
        icon_url=texts["icon_url"],
    )

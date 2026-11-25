"""Character tool handlers: list, add, update and remove (all admin-only, enforced in dispatch)."""
import logging
from datetime import UTC

from characters.core.configs import CharactersConfigs
from characters.core.errors import (
    CharacterConflictError,
    CharacterExistsError,
    CharacterNotFoundError,
    CharacterStorageError,
    InvalidCharacterError,
)
from characters.core.models import Character, CharacterChanges, CharacterOverview
from characters.core.service import CharacterService

log = logging.getLogger("characters.tools")


def _describe(overview: CharacterOverview) -> str:
    character = overview.character
    source = " by ".join(part for part in (character.song, character.artist) if part)
    tags = ("" if character.enabled else " [DISABLED]") + (" [INVALID]" if overview.problems else "")
    updated = f", updated {character.updated_at.astimezone(UTC):%Y-%m-%d %H:%M} UTC" if character.updated_at else ""
    lines = [f"- `{character.name}`{tags} ({source or character.display_name}): bound to {', '.join(map(str, character.bindings)) or 'nothing'}{updated}"]
    lines += [f"  problem: {problem}" for problem in overview.problems]
    if overview.unknown_skills:
        lines.append(f"  no such skill: {', '.join(overview.unknown_skills)}")
    return "\n".join(lines)


def _list_characters(service: CharacterService) -> str:
    overviews = service.list_characters()
    if not overviews:
        return "No characters are stored yet. Ask me to add one, or deploy a worker image that ships seed files."
    usable = sum(1 for o in overviews if o.character.enabled and not o.problems)
    header = f"{len(overviews)} character(s), {usable} in use. I can add, change and remove characters."
    return "\n".join([header, *(_describe(o) for o in overviews)])


def _texts(args: dict, keys: tuple[str, ...], problems: list[str]) -> dict[str, str | None]:
    """The text arguments among `keys`, stripped; None for one that was not given. A non-text is a problem."""
    found = {}
    for key in keys:
        value = args.get(key)
        if value is not None and not isinstance(value, str):
            problems.append(f"{key} must be a text")
            value = None
        found[key] = value.strip() if value is not None else None
    return found


def _text(args: dict, key: str) -> str | None:
    """One text argument, stripped; None when it is missing, null or not a text."""
    value = args.get(key)
    return value.strip() if isinstance(value, str) else None


def _text_list(args: dict, key: str, problems: list[str]) -> tuple[str, ...] | None:
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        problems.append(f"{key} must be a list of texts")
        return None
    return tuple(item.strip() for item in value)


def _voices(args: dict, problems: list[str]) -> dict:
    voices = args.get("voices")
    if voices is None:
        return {}
    if not isinstance(voices, dict) or not all(isinstance(block, dict) for block in voices.values()):
        problems.append("voices must be an object with one object of fields per feature, such as {'pr_reminder': {'headers': [...]}}")
        return {}
    return voices


def _flag(args: dict, key: str, default: bool | None, problems: list[str]) -> bool | None:
    """A true/false argument; an argument that is missing or null (models often send null for an optional one) is `default`."""
    value = args.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        problems.append(f"{key} must be true or false")
        return default
    return value


def _name_arg(args: dict) -> str:
    name = args.get("name")
    return name.strip() if isinstance(name, str) else ""


def _unknown_skills_note(unknown_skills: tuple[str, ...]) -> str:
    if not unknown_skills:
        return ""
    return f" No skill is named {', '.join(f'`{s}`' for s in unknown_skills)}, so those bindings match nothing."


def _problem_list(exc: InvalidCharacterError) -> str:
    return "\n".join(f"- {problem}" for problem in exc.problems)


def _build_character(
    name: str, display_name: str, bindings: tuple[str, ...], texts: dict[str, str | None], voices: dict, enabled: bool
) -> Character:
    """The Character for arguments that already passed the shape checks; a missing optional text is empty, a missing icon is None."""
    return Character(
        name=name,
        display_name=display_name,
        bindings=bindings,
        persona=texts["persona"] or "",
        voices=voices,
        song=texts["song"] or "",
        artist=texts["artist"] or "",
        enabled=enabled,
        icon_emoji=texts["icon_emoji"] or None,
        icon_url=texts["icon_url"] or None,
    )


def _parse_character(args: dict) -> tuple[Character | None, list[str]]:
    """Build a Character from the tool arguments, or say what is wrong with their shape. Business rules are the service's."""
    problems = []
    name = _name_arg(args)
    if not name:
        problems.append("name is required (a lower-case slug such as 'em-gai-mua')")
    display_name = _text(args, "display_name")
    if not display_name:
        problems.append("display_name is required (the sender name shown in Slack, such as the song title)")
    bindings = _text_list(args, "bindings", problems)
    if not args.get("bindings"):
        problems.append("bindings is required: say what the character speaks for ('feature:pr_reminder' or 'skill:<name>')")
    texts = _texts(args, CharactersConfigs.OPTIONAL_TEXT_FIELDS, problems)
    voices = _voices(args, problems)
    enabled = _flag(args, "enabled", True, problems)
    if problems:
        return None, problems
    return _build_character(name, display_name, bindings or (), texts, voices, enabled), []


def _parse_changes(args: dict, problems: list[str]) -> CharacterChanges:
    """Build the requested changes from the tool arguments; what is wrong with their shape is added to `problems`."""
    texts = _texts(args, ("display_name", *CharactersConfigs.OPTIONAL_TEXT_FIELDS), problems)
    return CharacterChanges(
        **{key: value for key, value in texts.items() if value is not None},
        enabled=_flag(args, "enabled", None, problems),
        bindings=_text_list(args, "bindings", problems),
        add_bindings=_text_list(args, "add_bindings", problems) or (),
        remove_bindings=_text_list(args, "remove_bindings", problems) or (),
        voices=_voices(args, problems),
    )


def _add_character(args: dict, service: CharacterService) -> str:
    character, problems = _parse_character(args)
    dry_run = _flag(args, "dry_run", False, problems)
    if character is None or problems:
        return "Not added. " + "; ".join(problems) + "."
    try:
        added = service.add(character, dry_run=dry_run)
    except InvalidCharacterError as exc:
        return "Not added. These problems need fixing:\n" + _problem_list(exc)
    except CharacterExistsError:
        return f"A character named `{character.name}` already exists, so nothing was changed. Pick another name, or edit that row in the `characters` table in Supabase."
    state = "enabled" if character.enabled else "disabled, so it won't be used until it is enabled"
    bound = f"bound to {', '.join(character.bindings)}"
    if dry_run:
        reply = f"Valid. Nothing was stored yet. `{character.name}` ({character.display_name}) would be added ({state}), {bound}."
    else:
        reply = f"Added `{character.name}` ({state}), {bound}."
    return reply + _unknown_skills_note(added.unknown_skills)


def _update_character(args: dict, service: CharacterService) -> str:
    name = _name_arg(args)
    problems = []
    changes = _parse_changes(args, problems)
    dry_run = _flag(args, "dry_run", False, problems)
    if not name:
        problems.append("name is required")
    if problems:
        return "Not changed. " + "; ".join(problems) + "."
    if changes.is_empty():
        return f"Nothing to change on `{name}`: say which fields, bindings or voices to change."
    try:
        updated = service.update(name, changes, dry_run=dry_run)
    except CharacterNotFoundError:
        return f"No character named `{name}` is stored. Use list_characters to see the names."
    except CharacterConflictError:
        return f"`{name}` was changed by someone else while this was being checked, so nothing was saved. Check it again with list_characters and repeat the change."
    except InvalidCharacterError as exc:
        return "Not changed. These problems need fixing:\n" + _problem_list(exc)
    character = updated.character
    state = "enabled" if character.enabled else "disabled"
    bound = f"bound to {', '.join(character.bindings) or 'nothing'}"
    reply = f"Valid. Nothing was changed yet. `{name}` would be {state}, {bound}." if dry_run else f"Changed `{name}` ({state}), now {bound}."
    return reply + _unknown_skills_note(updated.unknown_skills)


def _remove_character(args: dict, service: CharacterService) -> str:
    name = _name_arg(args)
    if not name:
        return "Not removed. name is required."
    try:
        removed = service.remove(name)
    except CharacterNotFoundError:
        return f"No character named `{name}` is stored. Use list_characters to see the names."
    reply = f"Removed `{name}`. Threads that used it get a new character next time."
    if removed.in_seed:
        reply += (
            " It ships as a seed file, so it comes back the next time a worker deploys. "
            "To keep it out for good, ask me to disable it instead (enabled false), or delete its seed file from the repo."
        )
    return reply


def dispatch_character_tool(name: str, args: dict, service: CharacterService) -> str:
    """Run one character tool and return the text to hand back to the LLM: a summary, a refusal or an error message."""
    try:
        if name == "list_characters":
            return _list_characters(service)
        if name == "add_character":
            return _add_character(args, service)
        if name == "update_character":
            return _update_character(args, service)
        if name == "remove_character":
            return _remove_character(args, service)
    except CharacterStorageError as exc:
        return str(exc)
    except Exception as exc:
        log.exception("Character tool %s failed", name)
        return f"Error executing {name}: {exc}"

    return f"Unknown character tool: {name}"

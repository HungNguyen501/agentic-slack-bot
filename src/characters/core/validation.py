"""Checks a character must pass before the bot uses it. Pure, no I/O.

Rows can be edited in Supabase without going through a sync, so these run every time a character is loaded, not only
when the seed files are read. The template rules keep the invariant of PR reminders: the only live mentions in a message
are the user IDs the code passes in, so a template can't carry a mention, a channel link, a URL or a broadcast word.
"""
import string
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from characters.core.configs import CharactersConfigs
from characters.core.models import Character


@dataclass(frozen=True)
class TemplateField:
    """One voice field: a template, or a list of them when `many`.

    `placeholders` are the only names a template may use. Each of `required` must appear, and so must at least one of
    `one_of` when it is given.
    """

    placeholders: frozenset[str] = frozenset()
    required: frozenset[str] = frozenset()
    one_of: frozenset[str] = frozenset()
    many: bool = False


@dataclass(frozen=True)
class VoiceSpec:
    """The fields a feature's voice block may have, and which of them it must have."""

    fields: Mapping[str, TemplateField]
    mandatory: frozenset[str] = frozenset()


def _text_problems(template: str) -> list[str]:
    """The rules about the text itself: its length, and nothing that makes Slack mention or link."""
    problems = []
    if len(template) > CharactersConfigs.MAX_TEMPLATE_LENGTH:
        problems.append(f"is {len(template)} characters, over the limit of {CharactersConfigs.MAX_TEMPLATE_LENGTH}")
    if "<" in template or ">" in template:
        problems.append("must not contain < or >")
    if CharactersConfigs.BROADCAST_RE.search(template):
        problems.append("must not contain @channel, @here or @everyone")
    if CharactersConfigs.URL_RE.search(template):
        problems.append("must not contain a link (http://, https://, any scheme://, www. or mailto:)")
    return problems


def _placeholder_problems(template: str, spec: TemplateField) -> list[str]:
    """The rules about the `{placeholders}`: a malformed template, plain names only, and the field's allowed and required set."""
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError as exc:
        return [f"is not a valid template ({exc})"]

    problems = []
    names = set()
    for _, name, format_spec, conversion in parsed:
        if name is None:
            continue
        if format_spec or conversion or not name.isidentifier():
            problems.append(f"has the placeholder {{{name}}} with an attribute, index or format; only plain names are allowed")
        else:
            names.add(name)
    if names - spec.placeholders:
        problems.append("uses unknown placeholders " + ", ".join(f"{{{name}}}" for name in sorted(names - spec.placeholders)))
    if spec.required - names:
        problems.append("is missing the placeholders " + ", ".join(f"{{{name}}}" for name in sorted(spec.required - names)))
    if spec.one_of and not names & spec.one_of:
        problems.append("needs one of " + ", ".join(f"{{{name}}}" for name in sorted(spec.one_of)))
    return problems


def _template_problems(template: Any, spec: TemplateField) -> list[str]:
    if not isinstance(template, str) or not template.strip():
        return ["must be a non-empty text"]
    return _text_problems(template) + _placeholder_problems(template, spec)


def _voice_problems(feature: str, block: Any, spec: VoiceSpec) -> list[str]:
    if not isinstance(block, Mapping):
        return [f"voices.{feature}: must be a set of fields"]
    problems = [f"voices.{feature}.{name}: is not a field of this voice" for name in block if name not in spec.fields]
    problems += [f"voices.{feature}.{name}: is required" for name in sorted(spec.mandatory) if name not in block]
    for name, value in block.items():
        field_spec = spec.fields.get(name)
        if field_spec is None:
            continue
        if field_spec.many:
            if not isinstance(value, list | tuple) or not 1 <= len(value) <= CharactersConfigs.MAX_TEMPLATES_PER_FIELD:
                problems.append(f"voices.{feature}.{name}: must be a list of 1 to {CharactersConfigs.MAX_TEMPLATES_PER_FIELD} templates")
                continue
            templates = list(enumerate(value, start=1))
            problems += [f"voices.{feature}.{name}[{i}]: {p}" for i, template in templates for p in _template_problems(template, field_spec)]
        else:
            problems += [f"voices.{feature}.{name}: {p}" for p in _template_problems(value, field_spec)]
    return problems


def _identity_problems(character: Character) -> list[str]:
    problems = []
    name = character.display_name
    if not name.strip() or len(name) > CharactersConfigs.MAX_DISPLAY_NAME_LENGTH or "<" in name or ">" in name:
        problems.append(f"display_name: must be 1 to {CharactersConfigs.MAX_DISPLAY_NAME_LENGTH} characters without < or >")
    if character.icon_emoji is not None and not CharactersConfigs.EMOJI_RE.fullmatch(character.icon_emoji):
        problems.append("icon_emoji: must look like :name:")
    url = character.icon_url
    if url is not None and not (url.startswith("https://") and len(url) <= CharactersConfigs.MAX_ICON_URL_LENGTH and not any(c.isspace() for c in url)):
        problems.append("icon_url: must be an https URL without spaces")
    return problems


def _binding_problems(character: Character, voices: Mapping[str, Any], specs: Mapping[str, VoiceSpec]) -> list[str]:
    problems = []
    for binding in character.bindings:
        if not isinstance(binding, str):
            problems.append(f"bindings: {binding!r} must be a text")
        elif binding.startswith(CharactersConfigs.FEATURE_PREFIX):
            feature = binding.removeprefix(CharactersConfigs.FEATURE_PREFIX)
            if feature not in specs:
                problems.append(f"bindings: {binding} is not a known feature ({', '.join(sorted(specs)) or 'none'})")
            elif feature not in voices:
                problems.append(f"bindings: {binding} needs a voices.{feature} block")
        elif not binding.startswith(CharactersConfigs.SKILL_PREFIX) or binding == CharactersConfigs.SKILL_PREFIX:
            problems.append(f"bindings: {binding!r} must be feature:<name> or skill:<name>")
    if any(isinstance(b, str) and b.startswith(CharactersConfigs.SKILL_PREFIX) for b in character.bindings) and not character.persona.strip():
        problems.append("persona: is required by a skill binding")
    return problems


def character_problems(character: Character, specs: Mapping[str, VoiceSpec]) -> list[str]:
    """Everything wrong with a character, one line per problem; empty when the bot can use it.

    `specs` maps each known feature to the voice fields it accepts. A row edited by hand can hold any JSON in `voices` and
    any elements in `bindings`, so neither is trusted: a wrong shape is a problem here, never an exception.
    """
    problems = []
    if not CharactersConfigs.NAME_RE.fullmatch(character.name):
        problems.append("name: must be lower-case letters, digits, - or _, up to 64 characters")
    problems += _identity_problems(character)
    if len(character.persona) > CharactersConfigs.MAX_PERSONA_LENGTH:
        problems.append(f"persona: is {len(character.persona)} characters, over the limit of {CharactersConfigs.MAX_PERSONA_LENGTH}")

    voices = character.voices
    if not isinstance(voices, Mapping):
        problems.append("voices: must be an object with one block per feature")
        voices = {}
    problems += _binding_problems(character, voices, specs)
    for feature, block in voices.items():
        if feature in specs:
            problems += _voice_problems(feature, block, specs[feature])
        else:
            problems.append(f"voices.{feature}: is not a known feature")
    return problems

"""Entities of the characters feature: a voice with an identity, bound to the features and skills it speaks for."""
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any
from characters.core.configs import CharactersConfigs


def feature_binding(feature: str) -> str:
    return f"{CharactersConfigs.FEATURE_PREFIX}{feature}"


def skill_binding(skill: str) -> str:
    return f"{CharactersConfigs.SKILL_PREFIX}{skill}"


@dataclass(frozen=True)
class Character:
    """A voice stored as a row of the `characters` table.

    `bindings` name where it speaks: `feature:<name>` for messages code posts from templates (its `voices[<name>]` block
    supplies them) and `skill:<name>` for agent replies shaped by a skill (its `persona` joins the system prompt).
    """

    name: str
    display_name: str
    bindings: tuple[str, ...] = ()
    persona: str = ""
    voices: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    song: str = ""
    artist: str = ""
    enabled: bool = True
    icon_emoji: str | None = None
    icon_url: str | None = None
    updated_at: datetime | None = None

    def is_bound_to_feature(self, feature: str) -> bool:
        return feature_binding(feature) in self.bindings

    def is_bound_to_skill(self, skill: str) -> bool:
        return skill_binding(skill) in self.bindings

    def slack_identity(self) -> dict[str, str]:
        """Keyword arguments for `connectors.slack.post_message` that post as this character.

        Slack lets an emoji override a URL, so only one icon is returned, the emoji first.
        """
        identity = {"username": self.display_name}
        if self.icon_emoji:
            identity["icon_emoji"] = self.icon_emoji
        elif self.icon_url:
            identity["icon_url"] = self.icon_url
        return identity


@dataclass(frozen=True)
class CharacterOverview:
    """A stored character as `list_characters` reports it: whether it can be used, and bindings that match no skill."""

    character: Character
    problems: tuple[str, ...]
    unknown_skills: tuple[str, ...]


@dataclass(frozen=True)
class SeedResult:
    added: tuple[str, ...]
    skipped: tuple[str, ...]


@dataclass(frozen=True)
class CharacterChanges:
    """What an admin asks to change on a stored character; a field left None stays as it is.

    `bindings` replaces the list, then `add_bindings` and `remove_bindings` are applied to the result. `voices` replaces
    the voice block of each feature it names and leaves the other features' blocks alone.
    """

    display_name: str | None = None
    song: str | None = None
    artist: str | None = None
    enabled: bool | None = None
    icon_emoji: str | None = None
    icon_url: str | None = None
    persona: str | None = None
    bindings: tuple[str, ...] | None = None
    add_bindings: tuple[str, ...] = ()
    remove_bindings: tuple[str, ...] = ()
    voices: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return self == CharacterChanges()

    def apply_to(self, current: Character) -> Character:
        """The character with these changes applied. Nothing is validated here; the caller checks the result as a whole.

        An empty `icon_emoji` or `icon_url` clears that icon. The bindings are replaced first when `bindings` is given,
        then `add_bindings` are appended (without duplicates) and `remove_bindings` taken out, so a binding that is both
        added and removed ends up removed.
        """
        fields = {key: getattr(self, key) for key in CharactersConfigs.PLAIN_FIELDS if getattr(self, key) is not None}
        fields |= {key: None for key in ("icon_emoji", "icon_url") if fields.get(key) == ""}
        bindings = tuple(self.bindings) if self.bindings is not None else current.bindings
        bindings = tuple(b for b in dict.fromkeys((*bindings, *self.add_bindings)) if b not in self.remove_bindings)
        return replace(current, **fields, bindings=bindings, voices={**current.voices, **self.voices})


@dataclass(frozen=True)
class RemovedCharacter:
    """A deleted character, and whether a seed file with its name ships in the image (which brings it back on the next deploy)."""

    name: str
    in_seed: bool

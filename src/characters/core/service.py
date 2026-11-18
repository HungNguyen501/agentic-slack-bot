"""Use cases of the characters feature: pick a voice for a feature or a skill, list the cast, seed an empty table."""
import logging
import random
from collections import Counter
from collections.abc import Iterable, Mapping

from characters.core.errors import (
    CharacterConflictError,
    CharacterExistsError,
    CharacterNotFoundError,
    CharacterStorageError,
    InvalidCharacterError,
    InvalidSeedError,
)
from characters.core.models import GLOBAL_SKILL, SKILL_PREFIX, Character, CharacterChanges, CharacterOverview, RemovedCharacter, SeedResult
from characters.core.ports import CharacterRepository, CharacterSource, KnownSkills, ThreadCharacterStore
from characters.core.validation import VoiceSpec, character_problems

log = logging.getLogger("characters")


class CharacterService:
    """Picks which character speaks, keeps one character per thread, and manages the cast.

    Picking never fails the caller: if the table or the thread store can't be read, the caller gets None and speaks in the
    default voice. A character that is disabled or fails validation is skipped, because rows can change in Supabase
    without passing through the seed.
    """

    def __init__(
        self,
        repository: CharacterRepository,
        thread_store: ThreadCharacterStore,
        known_skills: KnownSkills,
        source: CharacterSource,
        specs: Mapping[str, VoiceSpec],
        rng: random.Random | None = None,
    ) -> None:
        self._repository = repository
        self._thread_store = thread_store
        self._known_skills = known_skills
        self._source = source
        self._specs = specs
        self._rng = rng or random.Random()

    def usable_characters(self) -> list[Character]:
        """The enabled characters that pass validation, by name. An unreadable table gives none.

        A caller that picks for many threads in a row loads this once and passes it to `character_for_feature`, so the
        table is read, and a bad row warned about, once.
        """
        try:
            characters = self._repository.list_all()
        except CharacterStorageError:
            log.exception("Could not read the characters; speaking in the default voice")
            return []
        usable = []
        for character in characters:
            if not character.enabled:
                continue
            problems = character_problems(character, self._specs)
            if problems:
                log.warning("Skipping character %s: %s", character.name, "; ".join(problems))
                continue
            usable.append(character)
        return usable

    def _stored_name(self, channel: str, thread_ts: str) -> str | None:
        try:
            return self._thread_store.get(channel, thread_ts)
        except CharacterStorageError:
            log.warning("Could not read the character of thread %s/%s", channel, thread_ts, exc_info=True)
            return None

    def _remember(self, channel: str, thread_ts: str, character: Character) -> None:
        try:
            self._thread_store.set(channel, thread_ts, character.name)
        except CharacterStorageError:
            log.warning("Could not store the character of thread %s/%s", channel, thread_ts, exc_info=True)

    def character_for_feature(self, feature: str, channel: str, thread_ts: str, usable: list[Character] | None = None) -> Character | None:
        """The character that speaks for `feature` in a thread, or None for the default voice.

        A thread keeps the character it already has when that character can speak for the feature. Otherwise one is
        picked at random from those bound to the feature, or, when none is, from those bound to `skill:core` that have a
        voice for the feature, and remembered for the thread unless the thread already has a usable character.

        `usable` is the list from `usable_characters()` when the caller already has it; without it the table is read.
        """
        usable = self.usable_characters() if usable is None else usable
        stored = self._stored_name(channel, thread_ts)
        by_name = {c.name: c for c in usable}
        if stored in by_name and feature in by_name[stored].voices:
            return by_name[stored]

        candidates = [c for c in usable if c.is_bound_to_feature(feature)]
        if not candidates:
            candidates = [c for c in usable if c.is_bound_to_skill(GLOBAL_SKILL) and feature in c.voices]
        if not candidates:
            return None
        return self._pick(candidates, channel, thread_ts, remember=stored not in by_name)

    def character_for_skills(self, routed: Iterable[str], always: Iterable[str], channel: str, thread_ts: str) -> Character | None:
        """The character whose persona shapes an agent reply in a thread, or None for the plain prompt.

        A thread keeps the character it already has, if that character has a persona. Otherwise one is picked at random
        from the characters bound to a skill the router picked, or, when none is, from those bound to an always-loaded
        skill (`skill:core` is the default voice for every task), and remembered for the thread.
        """
        usable = [c for c in self.usable_characters() if c.persona.strip()]
        stored = self._stored_name(channel, thread_ts)
        by_name = {c.name: c for c in usable}
        if stored in by_name:
            return by_name[stored]

        routed_skills = set(routed)
        candidates = [c for c in usable if any(c.is_bound_to_skill(skill) for skill in routed_skills)]
        if not candidates:
            default_skills = {*always, GLOBAL_SKILL}
            candidates = [c for c in usable if any(c.is_bound_to_skill(skill) for skill in default_skills)]
        if not candidates:
            return None
        return self._pick(candidates, channel, thread_ts, remember=True)

    def _pick(self, candidates: list[Character], channel: str, thread_ts: str, *, remember: bool) -> Character:
        choice = self._rng.choice(candidates)
        if remember:
            self._remember(channel, thread_ts, choice)
        return choice

    def _unknown_skills(self, character: Character, known: frozenset[str]) -> tuple[str, ...]:
        return tuple(
            binding.removeprefix(SKILL_PREFIX)
            for binding in character.bindings
            if binding.startswith(SKILL_PREFIX) and binding.removeprefix(SKILL_PREFIX) not in known
        )

    def list_characters(self) -> list[CharacterOverview]:
        """Every stored character with the problems that keep the bot from using it.

        Raises:
            CharacterStorageError: the table couldn't be read.
        """
        known = self._known_skills.names()
        return [
            CharacterOverview(
                character=character,
                problems=tuple(character_problems(character, self._specs)),
                unknown_skills=self._unknown_skills(character, known),
            )
            for character in self._repository.list_all()
        ]

    def add(self, character: Character, *, dry_run: bool = False) -> CharacterOverview:
        """Store a new character. A name that is already stored is refused: adding never changes an existing row.

        A new character must be bound to at least one feature or skill, since one that speaks for nothing is never used.
        With `dry_run` every check runs and nothing is stored, so a caller can validate a request before asking for a yes.

        Returns:
            The character, with the bindings that match no skill (they are accepted but never match).

        Raises:
            InvalidCharacterError: the character fails validation; nothing is stored.
            CharacterExistsError: a character with this name is already stored.
            CharacterStorageError: the table couldn't be read or written.
        """
        problems = character_problems(character, self._specs)
        if not character.bindings:
            problems.append("bindings: a character needs at least one binding (feature:<name> or skill:<name>), or it is never used")
        if problems:
            raise InvalidCharacterError(problems)
        # Read before writing: a failure here must not leave a stored character the caller was told was an error.
        unknown_skills = self._unknown_skills(character, self._known_skills.names())
        if dry_run:
            if self._repository.get(character.name) is not None:
                raise CharacterExistsError(character.name)
        elif not self._repository.add_missing([character]):
            raise CharacterExistsError(character.name)
        return CharacterOverview(character, (), unknown_skills)

    def update(self, name: str, changes: CharacterChanges, *, dry_run: bool = False) -> CharacterOverview:
        """Change some fields of a stored character, leaving the rest as they are. The result must pass validation as a whole.

        With `dry_run` every check runs and nothing is stored.

        Raises:
            CharacterNotFoundError: no character has this name.
            InvalidCharacterError: the changed character fails validation; nothing is stored.
            CharacterConflictError: someone else changed the character after it was read; nothing is stored.
            CharacterStorageError: the table couldn't be read or written.
        """
        current = self._repository.get(name)
        if current is None:
            raise CharacterNotFoundError(name)
        updated = changes.apply_to(current)

        problems = character_problems(updated, self._specs)
        if problems:
            raise InvalidCharacterError(problems)
        unknown_skills = self._unknown_skills(updated, self._known_skills.names())
        if not dry_run and not self._repository.replace(updated):
            # Nothing matched: the row is gone, or someone else changed it after it was read above.
            if self._repository.get(name) is None:
                raise CharacterNotFoundError(name)
            raise CharacterConflictError(name)
        return CharacterOverview(updated, (), unknown_skills)

    def remove(self, name: str) -> RemovedCharacter:
        """Delete a stored character. Threads that had picked it get a new pick next time.

        A character with a seed file comes back on the next deploy, because the worker seeds missing names when it starts;
        `RemovedCharacter.in_seed` says so, and disabling the character keeps it out for good.

        Raises:
            CharacterNotFoundError: no character has this name.
            CharacterStorageError: the table couldn't be written.
        """
        if not self._repository.delete(name):
            raise CharacterNotFoundError(name)
        try:
            in_seed = any(c.name == name for c in self._source.read())
        except InvalidSeedError:
            in_seed = False
        return RemovedCharacter(name, in_seed)

    def seed(self) -> SeedResult:
        """Store the characters of the seed files whose name is missing from the table; existing rows are never changed.

        Raises:
            InvalidSeedError: there are no files, or a file is invalid or shares a name with another. Nothing is stored.
            CharacterStorageError: the table couldn't be written.
        """
        characters = self._source.read()
        if not characters:
            raise InvalidSeedError(["There are no character files to seed from."])
        problems = [f"{c.name}: {problem}" for c in characters for problem in character_problems(c, self._specs)]
        problems += [f"{name}: the name is used by more than one file" for name, n in Counter(c.name for c in characters).items() if n > 1]
        if problems:
            raise InvalidSeedError(problems)
        added = self._repository.add_missing(characters)
        return SeedResult(added=tuple(added), skipped=tuple(c.name for c in characters if c.name not in added))

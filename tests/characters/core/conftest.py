import random

import pytest

from characters.core.errors import CharacterStorageError
from characters.core.models import Character
from characters.core.service import CharacterService
from tests.support.characters import SPECS


class FakeRepository:
    def __init__(self, characters=()) -> None:
        self.characters = list(characters)
        self.error: CharacterStorageError | None = None
        self.added: list[Character] = []
        self.reads = 0
        self.before_replace = None  # a callable run just before replace(), to simulate a change by someone else

    def list_all(self) -> list[Character]:
        self.reads += 1
        if self.error:
            raise self.error
        return sorted(self.characters, key=lambda c: c.name)

    def get(self, name: str) -> Character | None:
        if self.error:
            raise self.error
        return next((c for c in self.characters if c.name == name), None)

    def replace(self, character: Character) -> bool:
        if self.error:
            raise self.error
        if self.before_replace:
            self.before_replace()
        index = next((i for i, c in enumerate(self.characters) if c.name == character.name), None)
        if index is None or self.characters[index].updated_at != character.updated_at:
            return False
        self.characters[index] = character
        return True

    def delete(self, name: str) -> bool:
        if self.error:
            raise self.error
        before = len(self.characters)
        self.characters = [c for c in self.characters if c.name != name]
        return len(self.characters) < before

    def add_missing(self, characters: list[Character]) -> list[str]:
        if self.error:
            raise self.error
        stored = {c.name for c in self.characters}
        new = [c for c in characters if c.name not in stored]
        self.characters += new
        self.added += new
        return [c.name for c in new]


class FakeThreadStore:
    def __init__(self) -> None:
        self.names: dict[tuple[str, str], str] = {}
        self.read_error: CharacterStorageError | None = None
        self.write_error: CharacterStorageError | None = None

    def get(self, channel: str, thread_ts: str) -> str | None:
        if self.read_error:
            raise self.read_error
        return self.names.get((channel, thread_ts))

    def set(self, channel: str, thread_ts: str, name: str) -> None:
        if self.write_error:
            raise self.write_error
        self.names[(channel, thread_ts)] = name


class FakeKnownSkills:
    def __init__(self, *names: str) -> None:
        self._names = frozenset(names)
        self.error: CharacterStorageError | None = None

    def names(self) -> frozenset[str]:
        if self.error:
            raise self.error
        return self._names


class FakeSource:
    def __init__(self, characters=()) -> None:
        self.characters = list(characters)

    def read(self) -> list[Character]:
        return self.characters


@pytest.fixture
def repository():
    return FakeRepository()


@pytest.fixture
def thread_store():
    return FakeThreadStore()


@pytest.fixture
def source():
    return FakeSource()


@pytest.fixture
def known_skills():
    return FakeKnownSkills("core", "metadata")


@pytest.fixture
def make_service(repository, thread_store, known_skills, source):
    def build() -> CharacterService:
        return CharacterService(repository, thread_store, known_skills, source, SPECS, random.Random(7))

    return build


@pytest.fixture
def service(make_service):
    return make_service()

class CharacterStorageError(Exception):
    """The characters table or the thread store couldn't be reached."""


class InvalidSeedError(Exception):
    """The seed files can't be stored; `problems` has one line per problem, each fit to show an admin."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


class InvalidCharacterError(Exception):
    """A character can't be added; `problems` has one line per problem, each fit to show an admin."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


class CharacterExistsError(Exception):
    """A character with this name is already stored; adding never overwrites it."""

    def __init__(self, name: str) -> None:
        super().__init__(f"A character named '{name}' already exists.")
        self.name = name


class CharacterNotFoundError(Exception):
    """No character with this name is stored."""

    def __init__(self, name: str) -> None:
        super().__init__(f"No character named '{name}' is stored.")
        self.name = name


class CharacterConflictError(Exception):
    """The character was changed by someone else after it was read, so the change was not saved."""

    def __init__(self, name: str) -> None:
        super().__init__(f"The character '{name}' was changed by someone else.")
        self.name = name

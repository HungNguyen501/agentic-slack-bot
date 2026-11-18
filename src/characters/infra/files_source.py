"""The seed files shipped in the image."""
from pathlib import Path

from characters.core.errors import InvalidSeedError
from characters.core.models import Character
from characters.infra.file_format import parse_character_file

SEED_DIR = Path(__file__).resolve().parents[1] / "files"


class FilesCharacterSource:
    def __init__(self, directory: Path = SEED_DIR) -> None:
        self._directory = directory

    def read(self) -> list[Character]:
        """Parse every `*.md` file of the directory, by file name.

        Raises:
            InvalidSeedError: the directory or a file can't be read, or a file can't be parsed; every file is tried so
                the error lists every problem.
        """
        try:
            paths = sorted(self._directory.glob("*.md"))
        except OSError as exc:
            raise InvalidSeedError([f"Could not read the character files: {exc}"]) from exc
        characters, problems = [], []
        for path in paths:
            try:
                characters.append(parse_character_file(path.name, path.read_text(encoding="utf-8")))
            except InvalidSeedError as exc:
                problems += exc.problems
            except (OSError, UnicodeDecodeError) as exc:
                problems.append(f"{path.name}: could not be read ({exc})")
        if problems:
            raise InvalidSeedError(problems)
        return characters

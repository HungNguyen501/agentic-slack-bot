import pytest

from characters.core.configs import CharactersConfigs
from agent.pr_reminders.message import VOICE_SPEC
from characters.core.errors import InvalidSeedError
from characters.core.validation import character_problems
from characters.infra.file_format import parse_character_file
from characters.infra.files_source import FilesCharacterSource

EXPECTED = {"dai-minh-tinh", "em-gai-mua", "exs-hate-me", "giua-dai-lo-dong-tay", "khong-sao-ma-em-day-roi"}


def write(directory, name, text):
    (directory / name).write_text(text, encoding="utf-8")


def test_the_source_reads_every_markdown_file_by_name(tmp_path):
    write(tmp_path, "b.md", "---\nname: b\n---\nB\n")
    write(tmp_path, "a.md", "---\nname: a\n---\nA\n")
    write(tmp_path, "notes.txt", "ignored")

    assert [c.name for c in FilesCharacterSource(tmp_path).read()] == ["a", "b"]


def test_an_empty_directory_gives_no_characters(tmp_path):
    assert FilesCharacterSource(tmp_path).read() == []


def test_every_bad_file_is_reported_in_one_error(tmp_path):
    write(tmp_path, "a.md", "---\nname: a\n---\n")
    write(tmp_path, "b.md", "no frontmatter")
    write(tmp_path, "c.md", "---\nenabled: nope\n---\n")

    with pytest.raises(InvalidSeedError) as error:
        FilesCharacterSource(tmp_path).read()

    assert sorted(p.split(":")[0] for p in error.value.problems) == ["b.md", "c.md", "c.md"]


def test_an_unreadable_file_is_a_problem_not_a_crash(tmp_path):
    (tmp_path / "bad.md").write_bytes(b"\xff\xfe\x00 not utf-8 \xff")

    with pytest.raises(InvalidSeedError) as error:
        FilesCharacterSource(tmp_path).read()

    assert error.value.problems[0].startswith("bad.md:")


def test_the_shipped_seed_files_are_the_five_characters():
    assert {c.name for c in FilesCharacterSource().read()} == EXPECTED


# The files are listed at collection time but only parsed inside the tests, so one broken file fails its own tests
# and leaves the rest of this module running.
SEED_FILES = sorted(path.name for path in CharactersConfigs.SEED_DIR.glob("*.md"))


def shipped(filename):
    return parse_character_file(filename, (CharactersConfigs.SEED_DIR / filename).read_text(encoding="utf-8"))


def test_the_five_seed_files_are_there():
    assert len(SEED_FILES) == 5


@pytest.mark.parametrize("filename", SEED_FILES)
def test_every_shipped_character_passes_validation(filename):
    assert character_problems(shipped(filename), {CharactersConfigs.FEATURE_PR_REMINDER: VOICE_SPEC}) == []


@pytest.mark.parametrize("filename", SEED_FILES)
def test_every_shipped_character_speaks_for_pr_reminders_and_only_there(filename):
    assert set(shipped(filename).bindings) == {"feature:pr_reminder", "skill:pr-reminders"}


@pytest.mark.parametrize("filename", SEED_FILES)
def test_every_shipped_character_has_a_persona_and_an_icon(filename):
    character = shipped(filename)

    assert character.persona
    assert character.icon_emoji

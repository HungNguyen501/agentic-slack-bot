import pytest

from characters.core.errors import InvalidSeedError
from characters.infra.file_format import parse_character_file

FULL = """---
name: em-gai-mua
display_name: Em Gái Mưa
song: Em Gái Mưa
artist: Hương Tràm
enabled: false
icon_emoji: ":cloud_with_rain:"
icon_url: https://x.test/a.png
bindings:
  - feature:pr_reminder
  - skill:core
pr_reminder:
  headers:
    - "{who} {subject}"
  author_line: "fix"
---

Speak softly.
Second line.
"""


def test_a_file_becomes_a_character():
    character = parse_character_file("em-gai-mua.md", FULL)

    assert (character.name, character.display_name, character.song, character.artist) == ("em-gai-mua", "Em Gái Mưa", "Em Gái Mưa", "Hương Tràm")
    assert (character.enabled, character.icon_emoji, character.icon_url) == (False, ":cloud_with_rain:", "https://x.test/a.png")
    assert character.bindings == ("feature:pr_reminder", "skill:core")
    assert character.persona == "Speak softly.\nSecond line."
    assert character.voices == {"pr_reminder": {"headers": ["{who} {subject}"], "author_line": "fix"}}


def test_optional_fields_default():
    character = parse_character_file("a.md", "---\nname: a\n---\nhi\n")

    assert (character.display_name, character.song, character.artist, character.enabled) == ("a", "", "", True)
    assert (character.icon_emoji, character.icon_url, character.bindings, dict(character.voices)) == (None, None, (), {})


def test_a_body_that_contains_a_frontmatter_fence_is_kept_whole():
    character = parse_character_file("a.md", "---\nname: a\n---\nbefore\n---\nafter\n")

    assert character.persona == "before\n---\nafter"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("name: a\n", "must start with a --- frontmatter block"),
        ("---\nname: a\n", "must start with a --- frontmatter block"),
        ("text\n---\nname: a\n---\n", "must start with a --- frontmatter block"),
        ("---\nname: [unclosed\n---\n", "not valid YAML"),
        ("---\n- a\n- b\n---\n", "must be a set of fields"),
        ("---\ndisplay_name: A\n---\n", "name is required"),
        ("---\nname: 5\n---\n", "name is required"),
        ("---\nname: a\nbindings: skill:core\n---\n", "bindings must be a list of texts"),
        ("---\nname: a\nbindings: [1]\n---\n", "bindings must be a list of texts"),
        ("---\nname: a\nenabled: maybe\n---\n", "enabled must be true or false"),
        ("---\nname: a\nsong: 5\n---\n", "song must be a text"),
        ("---\nname: a\nicon_emoji: [x]\n---\n", "icon_emoji must be a text"),
        ("---\nname: a\npr_reminder: oops\n---\n", "pr_reminder must be a voice block"),
    ],
)
def test_a_malformed_file_is_refused_with_its_file_name(text, expected):
    with pytest.raises(InvalidSeedError) as error:
        parse_character_file("bad.md", text)

    assert any(problem.startswith("bad.md:") and expected in problem for problem in error.value.problems)


def test_every_problem_of_a_file_is_reported_together():
    with pytest.raises(InvalidSeedError) as error:
        parse_character_file("bad.md", "---\nenabled: maybe\nbindings: x\n---\n")

    assert len(error.value.problems) == 3


def test_a_file_with_windows_line_endings_is_read_like_any_other():
    crlf = FULL.replace("\n", "\r\n")

    assert parse_character_file("a.md", crlf) == parse_character_file("a.md", FULL)

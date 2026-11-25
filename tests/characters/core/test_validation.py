import pytest

from characters.core.configs import CharactersConfigs
from characters.core.models import Character
from characters.core.validation import TemplateField, character_problems
from tests.support.characters import SPECS, make_character


def problems(**overrides) -> list[str]:
    return character_problems(make_character(**overrides), SPECS)


def test_a_valid_character_has_no_problems():
    assert problems() == []


def test_a_character_with_only_a_skill_binding_and_a_persona_is_valid():
    assert problems(bindings=("skill:core",), voices={}) == []


@pytest.mark.parametrize(
    "template, expected",
    [
        ("{who} {subject} {unknown}", "unknown placeholders {unknown}"),
        ("{subject} only", "missing the placeholders {who}"),
        ("{who} without a subject", "needs one of {subject}"),
        ("{who} {subject.x}", "attribute, index or format"),
        ("{who} {subject[0]}", "attribute, index or format"),
        ("{who} {subject!r}", "attribute, index or format"),
        ("{who} {subject:>10}", "attribute, index or format"),
        ("{who} {} {subject}", "attribute, index or format"),
        ("{who} {subject} <!channel>", "must not contain < or >"),
        ("{who} {subject} <@U123456789>", "must not contain < or >"),
        ("{who} {subject} <https://x.test|link>", "must not contain < or >"),
        ("{who} {subject} http://example.com", "must not contain a link"),
        ("{who} {subject} https://example.com/pull/1", "must not contain a link"),
        ("{who} {subject} HTTPS://EXAMPLE.COM", "must not contain a link"),
        ("{who} {subject} www.example.com", "must not contain a link"),
        ("{who} {subject} see (www.example.com)", "must not contain a link"),
        ("{who} {subject} ftp://files.example.com", "must not contain a link"),
        ("{who} {subject} mailto:someone@example.com", "must not contain a link"),
        ("{who} {subject} @channel", "@channel, @here or @everyone"),
        ("{who} {subject} @Here please", "@channel, @here or @everyone"),
        ("{who} {subject} @everyone", "@channel, @here or @everyone"),
        ("{who} {subject} {", "not a valid template"),
        ("", "non-empty"),
        ("   ", "non-empty"),
    ],
)
def test_a_header_template_is_refused(template, expected):
    found = problems(voices={"pr_reminder": {"headers": [template]}})

    assert any(expected in problem for problem in found), found


def test_a_template_over_the_length_limit_is_refused():
    found = problems(voices={"pr_reminder": {"headers": ["{who} {subject} " + "x" * CharactersConfigs.MAX_TEMPLATE_LENGTH]}})

    assert any("over the limit" in problem for problem in found)


def test_a_template_at_the_length_limit_is_accepted():
    template = "{who} {subject}" + "x" * (CharactersConfigs.MAX_TEMPLATE_LENGTH - len("{who} {subject}"))

    assert problems(voices={"pr_reminder": {"headers": [template]}}) == []


def test_a_problem_names_the_field_and_the_template_that_has_it():
    found = problems(voices={"pr_reminder": {"headers": ["{who} {subject}", "{who} nothing"]}})

    assert found == ["voices.pr_reminder.headers[2]: needs one of {subject}"]


def test_doubled_braces_are_literal_text_and_accepted():
    assert problems(voices={"pr_reminder": {"headers": ["{{literal}} {who} {subject}"]}}) == []


@pytest.mark.parametrize("headers", [[], ["{who} {subject}"] * 11, "{who} {subject}", None], ids=["empty", "too-many", "not-a-list", "none"])
def test_headers_must_be_a_list_of_one_to_ten_templates(headers):
    found = problems(voices={"pr_reminder": {"headers": headers}})

    assert any("must be a list of 1 to 10 templates" in problem for problem in found)


def test_a_single_template_field_checks_its_own_placeholders():
    found = problems(voices={"pr_reminder": {"headers": ["{who} {subject}"], "footer": "no id here"}})

    assert found == ["voices.pr_reminder.footer: is missing the placeholders {schedule_id}"]


def test_a_field_that_is_not_in_the_spec_is_refused():
    found = problems(voices={"pr_reminder": {"headers": ["{who} {subject}"], "surprise": "x"}})

    assert found == ["voices.pr_reminder.surprise: is not a field of this voice"]


def test_the_mandatory_field_must_be_present():
    found = problems(voices={"pr_reminder": {"plain": "x"}})

    assert found == ["voices.pr_reminder.headers: is required"]


def test_a_voice_block_must_be_a_set_of_fields():
    found = problems(voices={"pr_reminder": ["headers"]})

    assert found == ["voices.pr_reminder: must be a set of fields"]


def test_a_voice_for_an_unknown_feature_is_refused():
    found = problems(voices={"pr_reminder": {"headers": ["{who} {subject}"]}, "other": {}})

    assert found == ["voices.other: is not a known feature"]


def test_a_feature_binding_needs_its_voice_block():
    found = problems(voices={})

    assert found == ["bindings: feature:pr_reminder needs a voices.pr_reminder block"]


def test_a_feature_binding_must_name_a_known_feature():
    found = problems(bindings=("feature:nope",))

    assert any("feature:nope is not a known feature" in problem for problem in found)


@pytest.mark.parametrize("binding", ["pr_reminder", "tool:execute_query", "skill:", ""], ids=["no-prefix", "tool-prefix", "empty-skill", "empty"])
def test_a_binding_must_be_a_feature_or_a_skill(binding):
    found = problems(bindings=(binding,), voices={})

    assert any("must be feature:<name> or skill:<name>" in problem for problem in found)


def test_a_skill_binding_needs_a_persona():
    found = problems(bindings=("skill:core",), persona="  ", voices={})

    assert found == ["persona: is required by a skill binding"]


def test_a_persona_over_the_limit_is_refused():
    found = problems(persona="x" * (CharactersConfigs.MAX_PERSONA_LENGTH + 1))

    assert any("persona" in problem and "over the limit" in problem for problem in found)


@pytest.mark.parametrize("name", ["Upper", "has space", "-leading", "", "a" * 65, "ü"])
def test_a_name_must_be_a_slug(name):
    assert any(problem.startswith("name:") for problem in problems(name=name))


@pytest.mark.parametrize("display_name", ["", "  ", "x" * 81, "<@U123456789>", "a > b"])
def test_a_display_name_must_be_short_and_free_of_markup(display_name):
    assert any(problem.startswith("display_name:") for problem in problems(display_name=display_name))


@pytest.mark.parametrize("icon", ["star", ":Star:", ":two words:", "::", ":" + "a" * 61 + ":"])
def test_an_icon_emoji_must_look_like_an_emoji_code(icon):
    assert problems(icon_emoji=icon) == ["icon_emoji: must look like :name:"]


@pytest.mark.parametrize("url", ["http://x.test/a.png", "ftp://x.test", "https://x.test/a b.png", "https://" + "a" * 500])
def test_an_icon_url_must_be_a_short_https_url(url):
    assert problems(icon_url=url) == ["icon_url: must be an https URL without spaces"]


def test_a_valid_icon_is_accepted():
    assert problems(icon_emoji=":cloud_with_rain:", icon_url="https://x.test/a.png") == []


def test_every_problem_of_a_character_is_reported_together():
    character = Character(name="Bad Name", display_name="", bindings=("skill:core",), persona="")

    found = character_problems(character, {"pr_reminder": SPECS["pr_reminder"]})

    assert len(found) == 3


def test_a_spec_without_features_accepts_a_character_bound_only_to_skills():
    character = Character(name="a", display_name="A", bindings=("skill:core",), persona="hi")

    assert character_problems(character, {}) == []


def test_template_field_defaults_allow_no_placeholders():
    assert TemplateField().placeholders == frozenset()


# --- rows edited by hand ---------------------------------------------------------------------------


@pytest.mark.parametrize("voices", [[1], ["pr_reminder"], "pr_reminder", 5, 1.5, True], ids=["list", "list-of-name", "text", "number", "float", "bool"])
def test_voices_that_are_not_an_object_are_a_problem_not_an_exception(voices):
    found = problems(bindings=("skill:core",), voices=voices)

    assert found == ["voices: must be an object with one block per feature"]


def test_a_feature_binding_with_voices_that_are_not_an_object_reports_both_problems():
    found = problems(voices=[1])

    assert "voices: must be an object with one block per feature" in found
    assert "bindings: feature:pr_reminder needs a voices.pr_reminder block" in found


@pytest.mark.parametrize("element", [None, 5, ["skill:core"], {"a": 1}], ids=["null", "number", "list", "object"])
def test_a_binding_that_is_not_a_text_is_a_problem_not_an_exception(element):
    found = problems(bindings=("skill:core", element), voices={})

    assert any("must be a text" in problem for problem in found)


def test_a_null_binding_does_not_hide_the_other_problems_of_the_row():
    found = problems(bindings=(None, "skill:nope-prefix"), voices={}, persona="")

    assert any("must be a text" in problem for problem in found)
    assert "persona: is required by a skill binding" in found


@pytest.mark.parametrize("text", ["github.com", "see example.com/pull/1", "TP.HCM", "v1.2.3"])
def test_a_bare_domain_is_not_caught_because_it_cannot_be_told_from_ordinary_text(text):
    assert problems(voices={"pr_reminder": {"headers": [f"{{who}} {{subject}} {text}"]}}) == []


def test_a_url_in_the_persona_is_not_a_template_problem():
    assert problems(bindings=("skill:core",), voices={}, persona="See https://example.com for the style guide.") == []

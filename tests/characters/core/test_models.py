import pytest

from characters.core.models import Character, CharacterChanges


def test_slack_identity_is_the_display_name_and_the_emoji():
    character = Character(name="a", display_name="Anna", icon_emoji=":star:", icon_url="https://x.test/a.png")

    assert character.slack_identity() == {"username": "Anna", "icon_emoji": ":star:"}


def test_slack_identity_falls_back_to_the_icon_url():
    character = Character(name="a", display_name="Anna", icon_url="https://x.test/a.png")

    assert character.slack_identity() == {"username": "Anna", "icon_url": "https://x.test/a.png"}


def test_slack_identity_without_an_icon_is_only_the_name():
    assert Character(name="a", display_name="Anna").slack_identity() == {"username": "Anna"}


def test_bindings_are_matched_by_kind_and_name():
    character = Character(name="a", display_name="A", bindings=("feature:pr_reminder", "skill:core"))

    assert character.is_bound_to_feature("pr_reminder")
    assert not character.is_bound_to_feature("core")
    assert character.is_bound_to_skill("core")
    assert not character.is_bound_to_skill("pr_reminder")


BASE = Character(
    name="a", display_name="A", song="Old", artist="Artist", enabled=True, icon_emoji=":star:", icon_url="https://x.test/a.png",
    persona="old persona", bindings=("feature:pr_reminder", "skill:core"),
    voices={"pr_reminder": {"headers": ["{who} {subject} old"]}, "other": {"x": "y"}},
)  # fmt: skip


def test_changes_that_say_nothing_leave_the_character_as_it_is():
    assert CharacterChanges().apply_to(BASE) == BASE


@pytest.mark.parametrize(
    "field, value",
    [
        ("display_name", "New"),
        ("song", ""),
        ("artist", "Other"),
        ("enabled", False),
        ("persona", "new persona"),
        ("icon_emoji", ":moon:"),
        ("icon_url", "https://x.test/b.png"),
    ],
)
def test_a_plain_field_is_replaced_and_the_others_are_kept(field, value):
    updated = CharacterChanges(**{field: value}).apply_to(BASE)

    assert getattr(updated, field) == value
    assert updated == Character(**{**vars(BASE), field: value})


@pytest.mark.parametrize("icon", ["icon_emoji", "icon_url"])
def test_an_empty_icon_clears_that_icon_only(icon):
    updated = CharacterChanges(**{icon: ""}).apply_to(BASE)

    assert getattr(updated, icon) is None
    assert (updated.icon_emoji is None) != (updated.icon_url is None)


def test_the_bindings_can_be_replaced():
    assert CharacterChanges(bindings=("skill:metadata",)).apply_to(BASE).bindings == ("skill:metadata",)


def test_the_bindings_can_be_emptied():
    assert CharacterChanges(bindings=()).apply_to(BASE).bindings == ()


def test_bindings_are_added_after_the_existing_ones_without_duplicates():
    updated = CharacterChanges(add_bindings=("skill:metadata", "skill:core")).apply_to(BASE)

    assert updated.bindings == ("feature:pr_reminder", "skill:core", "skill:metadata")


def test_bindings_are_removed_and_a_missing_one_is_ignored():
    updated = CharacterChanges(remove_bindings=("feature:pr_reminder", "skill:nope")).apply_to(BASE)

    assert updated.bindings == ("skill:core",)


def test_a_replacement_is_applied_before_the_additions_and_removals():
    changes = CharacterChanges(bindings=("skill:jobs",), add_bindings=("skill:metadata",), remove_bindings=("skill:jobs",))

    assert changes.apply_to(BASE).bindings == ("skill:metadata",)


def test_a_binding_that_is_added_and_removed_ends_up_removed():
    changes = CharacterChanges(add_bindings=("skill:jobs",), remove_bindings=("skill:jobs",))

    assert changes.apply_to(BASE).bindings == ("feature:pr_reminder", "skill:core")


def test_a_voice_block_replaces_the_block_of_its_feature_and_keeps_the_others():
    updated = CharacterChanges(voices={"pr_reminder": {"headers": ["{who} {subject} new"]}}).apply_to(BASE)

    assert updated.voices == {"pr_reminder": {"headers": ["{who} {subject} new"]}, "other": {"x": "y"}}


def test_applying_changes_does_not_modify_the_original():
    CharacterChanges(display_name="New", add_bindings=("skill:jobs",), voices={"pr_reminder": {}}).apply_to(BASE)

    assert BASE.display_name == "A"
    assert "skill:jobs" not in BASE.bindings
    assert BASE.voices["pr_reminder"]["headers"] == ["{who} {subject} old"]

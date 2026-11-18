from datetime import UTC, datetime
from unittest.mock import create_autospec

import pytest

from characters.core.errors import CharacterConflictError, CharacterExistsError, CharacterNotFoundError, CharacterStorageError, InvalidCharacterError
from characters.core.models import CharacterChanges, CharacterOverview, RemovedCharacter
from characters.core.service import CharacterService
from characters.presentation import tools
from tests.support.characters import make_character


@pytest.fixture
def service():
    return create_autospec(CharacterService, instance=True)


def overview(name="a", problems=(), unknown_skills=(), **overrides):
    character = make_character(name, song="Song", artist="Artist", updated_at=datetime(2026, 11, 18, 3, 0, tzinfo=UTC), **overrides)
    return CharacterOverview(character, problems, unknown_skills)


def test_list_says_when_nothing_is_stored(service):
    service.list_characters.return_value = []

    assert "No characters are stored yet" in tools.dispatch_character_tool("list_characters", {}, service)


def test_list_shows_each_character_with_its_song_bindings_and_update_time(service):
    service.list_characters.return_value = [overview("a", bindings=("feature:pr_reminder", "skill:core"))]

    reply = tools.dispatch_character_tool("list_characters", {}, service)

    assert "1 character(s), 1 in use" in reply
    assert "`a` (Song by Artist): bound to feature:pr_reminder, skill:core, updated 2026-11-18 03:00 UTC" in reply


def test_list_marks_disabled_and_invalid_characters_and_gives_the_reasons(service):
    service.list_characters.return_value = [
        overview("off", enabled=False),
        overview("bad", problems=("persona: is required by a skill binding",), unknown_skills=("ghost",)),
        overview("ok"),
    ]

    reply = tools.dispatch_character_tool("list_characters", {}, service)

    assert "2 in use" not in reply and "1 in use" in reply
    assert "`off` [DISABLED]" in reply
    assert "`bad` [INVALID]" in reply
    assert "problem: persona: is required by a skill binding" in reply
    assert "no such skill: ghost" in reply


def test_list_shows_a_row_with_a_malformed_binding_instead_of_failing(service):
    service.list_characters.return_value = [overview("bad", problems=("bindings: None must be a text",), bindings=("skill:core", None))]

    reply = tools.dispatch_character_tool("list_characters", {}, service)

    assert "`bad` [INVALID]" in reply and "skill:core, None" in reply


def test_list_shows_a_character_without_bindings(service):
    service.list_characters.return_value = [overview(bindings=())]

    assert "bound to nothing" in tools.dispatch_character_tool("list_characters", {}, service)


ARGS = {
    "name": "anna",
    "display_name": "Anna",
    "song": "A Song",
    "artist": "An Artist",
    "icon_emoji": ":star:",
    "bindings": ["feature:pr_reminder", "skill:metadata"],
    "persona": "Speak like Anna.",
    "voices": {"pr_reminder": {"headers": ["{who} {subject}"]}},
}


def add(service, **overrides):
    removed = {key for key, value in overrides.items() if value is KEEP_OUT}
    args = {k: v for k, v in (ARGS | overrides).items() if k not in removed}
    return tools.dispatch_character_tool("add_character", args, service)


KEEP_OUT = object()


def test_add_builds_the_character_from_the_arguments_and_reports_it(service):
    service.add.return_value = CharacterOverview(make_character("anna"), (), ())

    reply = add(service)

    stored = service.add.call_args.args[0]
    assert (stored.name, stored.display_name, stored.song, stored.artist) == ("anna", "Anna", "A Song", "An Artist")
    assert (stored.icon_emoji, stored.icon_url, stored.enabled) == (":star:", None, True)
    assert stored.bindings == ("feature:pr_reminder", "skill:metadata")
    assert (stored.persona, dict(stored.voices)) == ("Speak like Anna.", {"pr_reminder": {"headers": ["{who} {subject}"]}})
    assert reply == "Added `anna` (enabled), bound to feature:pr_reminder, skill:metadata."


def test_add_needs_only_a_name_a_display_name_and_a_binding(service):
    service.add.return_value = CharacterOverview(make_character("min"), (), ())

    tools.dispatch_character_tool("add_character", {"name": " min ", "display_name": " Min ", "bindings": [" skill:core "]}, service)

    stored = service.add.call_args.args[0]
    assert (stored.name, stored.display_name, stored.bindings, stored.persona, dict(stored.voices), stored.enabled) == ("min", "Min", ("skill:core",), "", {}, True)
    assert (stored.icon_emoji, stored.icon_url, stored.song, stored.artist) == (None, None, "", "")
    assert service.add.call_args.kwargs == {"dry_run": False}


def test_add_asks_for_everything_that_is_missing_in_one_reply(service):
    reply = tools.dispatch_character_tool("add_character", {}, service)

    assert reply.startswith("Not added.")
    assert "name is required" in reply and "display_name is required" in reply and "bindings is required" in reply
    service.add.assert_not_called()


@pytest.mark.parametrize("bindings", [KEEP_OUT, [], None], ids=["missing", "empty", "none"])
def test_add_without_a_binding_is_refused_before_asking_the_service(service, bindings):
    reply = add(service, bindings=bindings)

    assert "bindings is required" in reply
    service.add.assert_not_called()


def test_add_stores_a_blank_icon_as_none(service):
    service.add.return_value = CharacterOverview(make_character("anna"), (), ())

    add(service, icon_emoji="  ", icon_url="")

    assert (service.add.call_args.args[0].icon_emoji, service.add.call_args.args[0].icon_url) == (None, None)


def test_add_can_store_a_character_disabled(service):
    service.add.return_value = CharacterOverview(make_character("anna", enabled=False), (), ())

    reply = add(service, enabled=False)

    assert service.add.call_args.args[0].enabled is False
    assert "(disabled, so it won't be used until it is enabled)" in reply


def test_add_warns_about_bindings_to_skills_that_do_not_exist(service):
    service.add.return_value = CharacterOverview(make_character("anna"), (), ("ghost", "phantom"))

    assert "No skill is named `ghost`, `phantom`, so those bindings match nothing." in add(service)


def test_add_lists_every_problem_the_rules_found(service):
    service.add.side_effect = InvalidCharacterError(["display_name: too long", "persona: is required by a skill binding"])

    reply = add(service)

    assert reply.startswith("Not added.")
    assert "- display_name: too long" in reply and "- persona: is required by a skill binding" in reply


def test_add_says_when_the_name_is_taken_and_changes_nothing(service):
    service.add.side_effect = CharacterExistsError("anna")

    reply = add(service)

    assert "`anna` already exists" in reply and "nothing was changed" in reply and "Supabase" in reply


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"name": KEEP_OUT}, "name is required"),
        ({"name": "  "}, "name is required"),
        ({"name": 5}, "name is required"),
        ({"display_name": 5}, "display_name is required"),
        ({"display_name": "  "}, "display_name is required"),
        ({"dry_run": "yes"}, "dry_run must be true or false"),
        ({"persona": ["x"]}, "persona must be a text"),
        ({"icon_emoji": 5}, "icon_emoji must be a text"),
        ({"bindings": "skill:core"}, "bindings must be a list of texts"),
        ({"bindings": [1]}, "bindings must be a list of texts"),
        ({"voices": ["headers"]}, "voices must be an object"),
        ({"voices": {"pr_reminder": "oops"}}, "voices must be an object"),
        ({"enabled": "yes"}, "enabled must be true or false"),
    ],
    ids=[
        "no-name", "blank-name", "number-name", "display-name", "blank-display-name", "dry-run", "persona", "icon", "bindings-string",
        "bindings-number", "voices-list", "voice-block", "enabled",
    ],
)
def test_add_refuses_arguments_of_the_wrong_shape_before_asking_the_service(service, overrides, expected):
    reply = add(service, **overrides)

    assert reply.startswith("Not added.") and expected in reply
    service.add.assert_not_called()


def test_add_reports_every_shape_problem_together(service):
    reply = tools.dispatch_character_tool("add_character", {"enabled": "x", "bindings": "y"}, service)

    assert reply.count(";") == 3
    assert "bindings must be a list of texts" in reply


def test_the_list_says_the_agent_can_add_change_and_remove_characters(service):
    service.list_characters.return_value = [overview()]

    assert "I can add, change and remove characters." in tools.dispatch_character_tool("list_characters", {}, service)


def test_a_storage_error_is_shown_to_the_admin(service):
    service.list_characters.side_effect = service.add.side_effect = CharacterStorageError("Could not read the characters: down")

    assert tools.dispatch_character_tool("list_characters", {}, service) == "Could not read the characters: down"
    assert add(service) == "Could not read the characters: down"


def test_an_unexpected_error_is_returned_not_raised(service):
    service.list_characters.side_effect = RuntimeError("boom")

    assert tools.dispatch_character_tool("list_characters", {}, service) == "Error executing list_characters: boom"


def test_an_unknown_tool_name_is_reported(service):
    assert tools.dispatch_character_tool("nope", {}, service) == "Unknown character tool: nope"


# --- update_character ------------------------------------------------------------------------------


def update(service, **args):
    return tools.dispatch_character_tool("update_character", {"name": "anna"} | args, service)


def test_update_passes_only_the_requested_changes_to_the_service(service):
    service.update.return_value = CharacterOverview(make_character("anna", bindings=("skill:metadata",)), (), ())

    reply = update(service, persona=" New persona ", add_bindings=["skill:metadata"], remove_bindings=["feature:pr_reminder"], enabled=False)

    name, changes = service.update.call_args.args
    assert name == "anna"
    assert changes == CharacterChanges(persona="New persona", enabled=False, add_bindings=("skill:metadata",), remove_bindings=("feature:pr_reminder",))
    assert reply == "Changed `anna` (enabled), now bound to skill:metadata."


def test_update_passes_a_replacement_list_and_voices(service):
    service.update.return_value = CharacterOverview(make_character("anna"), (), ())

    update(service, bindings=["skill:core"], voices={"pr_reminder": {"headers": ["{who} {subject}"]}})

    changes = service.update.call_args.args[1]
    assert changes.bindings == ("skill:core",)
    assert changes.voices == {"pr_reminder": {"headers": ["{who} {subject}"]}}


def test_update_reports_a_disabled_character_and_unknown_skills(service):
    service.update.return_value = CharacterOverview(make_character("anna", enabled=False, bindings=("skill:ghost",)), (), ("ghost",))

    reply = update(service, enabled=False)

    assert "(disabled)" in reply and "No skill is named `ghost`" in reply


def test_update_without_any_change_asks_what_to_change(service):
    reply = update(service)

    assert "Nothing to change on `anna`" in reply
    service.update.assert_not_called()


def test_update_of_an_unknown_character_points_to_the_list(service):
    service.update.side_effect = CharacterNotFoundError("anna")

    assert "No character named `anna` is stored. Use list_characters" in update(service, enabled=False)


def test_update_says_when_someone_else_changed_the_character_meanwhile(service):
    service.update.side_effect = CharacterConflictError("anna")

    reply = update(service, enabled=False)

    assert "was changed by someone else" in reply and "nothing was saved" in reply


def test_update_lists_every_problem_when_the_result_is_invalid(service):
    service.update.side_effect = InvalidCharacterError(["persona: is required by a skill binding", "icon_emoji: must look like :name:"])

    reply = update(service, persona="")

    assert reply.startswith("Not changed.")
    assert "- persona: is required by a skill binding" in reply and "- icon_emoji: must look like :name:" in reply


@pytest.mark.parametrize(
    "args, expected",
    [
        ({"name": "  "}, "name is required"),
        ({"persona": 5}, "persona must be a text"),
        ({"enabled": "yes"}, "enabled must be true or false"),
        ({"bindings": "skill:core"}, "bindings must be a list of texts"),
        ({"add_bindings": [1]}, "add_bindings must be a list of texts"),
        ({"remove_bindings": "x"}, "remove_bindings must be a list of texts"),
        ({"voices": ["x"]}, "voices must be an object"),
    ],
    ids=["no-name", "persona", "enabled", "bindings", "add", "remove", "voices"],
)
def test_update_refuses_arguments_of_the_wrong_shape(service, args, expected):
    reply = update(service, **args)

    assert reply.startswith("Not changed.") and expected in reply
    service.update.assert_not_called()


# --- remove_character ------------------------------------------------------------------------------


def remove(service, **args):
    return tools.dispatch_character_tool("remove_character", {"name": "anna"} | args, service)


def test_remove_deletes_the_character_and_says_threads_get_a_new_one(service):
    service.remove.return_value = RemovedCharacter("anna", in_seed=False)

    reply = remove(service)

    service.remove.assert_called_once_with("anna")
    assert reply == "Removed `anna`. Threads that used it get a new character next time."


def test_remove_warns_that_a_seed_character_comes_back_on_the_next_deploy(service):
    service.remove.return_value = RemovedCharacter("anna", in_seed=True)

    reply = remove(service)

    assert "comes back the next time a worker deploys" in reply
    assert "disable it instead" in reply


def test_remove_of_an_unknown_character_points_to_the_list(service):
    service.remove.side_effect = CharacterNotFoundError("anna")

    assert "No character named `anna` is stored" in remove(service)


def test_remove_needs_a_name(service):
    assert remove(service, name=" ") == "Not removed. name is required."
    service.remove.assert_not_called()


# --- dry runs --------------------------------------------------------------------------------------


def test_add_with_dry_run_validates_without_storing_and_says_so(service):
    service.add.return_value = CharacterOverview(make_character("anna"), (), ())

    reply = add(service, dry_run=True)

    assert service.add.call_args.kwargs == {"dry_run": True}
    assert reply == "Valid. Nothing was stored yet. `anna` (Anna) would be added (enabled), bound to feature:pr_reminder, skill:metadata."


def test_add_with_dry_run_still_reports_problems_and_taken_names(service):
    service.add.side_effect = InvalidCharacterError(["persona: is required by a skill binding"])
    assert "- persona: is required by a skill binding" in add(service, dry_run=True)

    service.add.side_effect = CharacterExistsError("anna")
    assert "already exists" in add(service, dry_run=True)


def test_update_with_dry_run_validates_without_storing_and_says_so(service):
    service.update.return_value = CharacterOverview(make_character("anna", bindings=("skill:metadata",)), (), ())

    reply = update(service, add_bindings=["skill:metadata"], dry_run=True)

    assert service.update.call_args.kwargs == {"dry_run": True}
    assert reply == "Valid. Nothing was changed yet. `anna` would be enabled, bound to skill:metadata."


def test_update_runs_for_real_unless_asked_for_a_dry_run(service):
    service.update.return_value = CharacterOverview(make_character("anna"), (), ())

    update(service, enabled=True)

    assert service.update.call_args.kwargs == {"dry_run": False}


# --- null means "not given" ------------------------------------------------------------------------


@pytest.fixture
def stored_character(service):
    """The overview the service answers add and update with, so the handlers get as far as their reply."""
    overview = CharacterOverview(make_character("anna"), (), ())
    service.add.return_value = service.update.return_value = overview
    return overview


@pytest.mark.parametrize("key", ["enabled", "dry_run", "voices", "song", "artist", "icon_emoji", "icon_url", "persona"])
def test_add_gives_the_service_the_same_character_for_a_null_argument_as_for_a_missing_one(service, stored_character, key):
    add(service, **{key: KEEP_OUT})
    expected = service.add.call_args

    service.add.reset_mock()
    add(service, **{key: None})

    assert service.add.call_args == expected


def test_add_never_hands_the_service_a_null_enabled_flag(service, stored_character):
    add(service, enabled=None)

    assert service.add.call_args.args[0].enabled is True


def test_add_with_a_null_dry_run_is_a_real_run(service, stored_character):
    add(service, dry_run=None)

    assert service.add.call_args.kwargs == {"dry_run": False}


@pytest.mark.parametrize("key", ["enabled", "dry_run", "voices", "bindings", "add_bindings", "remove_bindings", "persona", "display_name"])
def test_update_with_only_null_arguments_asks_what_to_change(service, stored_character, key):
    reply = update(service, **{key: None})

    assert "Nothing to change" in reply
    service.update.assert_not_called()


def test_update_ignores_the_nulls_next_to_a_real_change(service, stored_character):
    update(service, persona="New", enabled=None, bindings=None, voices=None, dry_run=None)

    assert service.update.call_args.args[1] == CharacterChanges(persona="New")
    assert service.update.call_args.kwargs == {"dry_run": False}

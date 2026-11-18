import logging
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from characters.core.errors import CharacterConflictError, CharacterExistsError, CharacterNotFoundError, CharacterStorageError, InvalidCharacterError, InvalidSeedError
from characters.core.models import CharacterChanges
from tests.support.characters import make_character

CH, TS = "C1", "1.0"
EARLIER = datetime(2026, 11, 18, 3, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 18, 4, 0, tzinfo=UTC)


def skill_character(name, *skills, **overrides):
    return make_character(name, **{"bindings": tuple(f"skill:{s}" for s in skills), "voices": {}} | overrides)


# --- character_for_feature -------------------------------------------------------------------------


def test_a_feature_without_a_bound_character_gets_none(service, repository):
    repository.characters = [skill_character("a", "metadata")]

    assert service.character_for_feature("pr_reminder", CH, TS) is None


def test_a_feature_picks_among_the_characters_bound_to_it(service, repository):
    repository.characters = [make_character("a"), make_character("b"), skill_character("c", "core", voices={"pr_reminder": {"headers": ["{who} {subject}"]}})]

    picked = {service.character_for_feature("pr_reminder", CH, f"{n}.0").name for n in range(40)}

    assert picked == {"a", "b"}


def test_a_thread_keeps_the_character_picked_for_it(service, repository, thread_store):
    repository.characters = [make_character("a"), make_character("b"), make_character("c")]

    first = service.character_for_feature("pr_reminder", CH, TS)

    assert {service.character_for_feature("pr_reminder", CH, TS).name for _ in range(20)} == {first.name}
    assert thread_store.names == {(CH, TS): first.name}


def test_different_threads_are_picked_independently(service, repository, thread_store):
    repository.characters = [make_character("a"), make_character("b")]

    for n in range(40):
        service.character_for_feature("pr_reminder", CH, f"{n}.0")

    assert set(thread_store.names.values()) == {"a", "b"}


def test_the_same_thread_ts_in_another_channel_is_another_thread(service, repository, thread_store):
    repository.characters = [make_character("a")]
    thread_store.names[("C2", TS)] = "elsewhere"

    assert service.character_for_feature("pr_reminder", CH, TS).name == "a"


def test_a_stored_character_that_can_speak_for_the_feature_is_used_even_if_not_bound_to_it(service, repository, thread_store):
    repository.characters = [make_character("a"), skill_character("b", "core", voices={"pr_reminder": {"headers": ["{who} {subject}"]}})]
    thread_store.names[(CH, TS)] = "b"

    assert service.character_for_feature("pr_reminder", CH, TS).name == "b"


def test_a_stored_character_without_the_voice_is_not_used_and_not_overwritten(service, repository, thread_store):
    repository.characters = [make_character("a"), skill_character("b", "core")]
    thread_store.names[(CH, TS)] = "b"

    assert service.character_for_feature("pr_reminder", CH, TS).name == "a"
    assert thread_store.names[(CH, TS)] == "b"


@pytest.mark.parametrize("fix", ["delete", "disable", "invalidate"])
def test_a_stored_character_that_is_gone_disabled_or_invalid_is_replaced(service, repository, thread_store, fix):
    repository.characters = [make_character("a"), make_character("b")]
    thread_store.names[(CH, TS)] = "b"
    if fix == "delete":
        repository.characters = [make_character("a")]
    elif fix == "disable":
        repository.characters = [make_character("a"), make_character("b", enabled=False)]
    else:
        repository.characters = [make_character("a"), make_character("b", voices={"pr_reminder": {"headers": ["no placeholders"]}})]

    assert service.character_for_feature("pr_reminder", CH, TS).name == "a"
    assert thread_store.names[(CH, TS)] == "a"


def test_the_global_voice_is_the_fallback_for_a_feature_when_it_has_that_voice(service, repository):
    repository.characters = [skill_character("g", "core", voices={"pr_reminder": {"headers": ["{who} {subject}"]}}), skill_character("h", "core")]

    assert service.character_for_feature("pr_reminder", CH, TS).name == "g"


def test_a_feature_character_wins_over_the_global_voice(service, repository):
    repository.characters = [make_character("a"), skill_character("g", "core", voices={"pr_reminder": {"headers": ["{who} {subject}"]}})]

    assert {service.character_for_feature("pr_reminder", CH, f"{n}.0").name for n in range(20)} == {"a"}


def test_disabled_and_invalid_characters_are_never_picked_for_a_feature(service, repository, caplog):
    repository.characters = [
        make_character("off", enabled=False),
        make_character("bad", voices={"pr_reminder": {"headers": ["<!channel> {who} {subject}"]}}),
        make_character("ok"),
    ]

    with caplog.at_level(logging.WARNING, logger="characters"):
        picked = {service.character_for_feature("pr_reminder", CH, f"{n}.0").name for n in range(20)}

    assert picked == {"ok"}
    assert "Skipping character bad" in caplog.text


def test_an_unreadable_table_means_the_default_voice(service, repository, thread_store):
    repository.error = CharacterStorageError("down")

    assert service.character_for_feature("pr_reminder", CH, TS) is None
    assert thread_store.names == {}


def test_an_unreadable_thread_store_still_picks_a_character(service, repository, thread_store):
    repository.characters = [make_character("a")]
    thread_store.read_error = CharacterStorageError("redis down")

    assert service.character_for_feature("pr_reminder", CH, TS).name == "a"


def test_an_unwritable_thread_store_still_returns_the_character(service, repository, thread_store):
    repository.characters = [make_character("a")]
    thread_store.write_error = CharacterStorageError("redis down")

    assert service.character_for_feature("pr_reminder", CH, TS).name == "a"


# --- character_for_skills --------------------------------------------------------------------------


def test_a_routed_skill_picks_among_the_characters_bound_to_it(service, repository):
    repository.characters = [skill_character("m1", "metadata"), skill_character("m2", "metadata"), skill_character("g", "core")]

    picked = {service.character_for_skills(["metadata"], ["core"], CH, f"{n}.0").name for n in range(40)}

    assert picked == {"m1", "m2"}


def test_several_routed_skills_pool_their_characters(service, repository):
    repository.characters = [skill_character("m", "metadata"), skill_character("j", "jobs"), skill_character("g", "core")]

    picked = {service.character_for_skills(["metadata", "jobs"], ["core"], CH, f"{n}.0").name for n in range(40)}

    assert picked == {"m", "j"}


def test_the_global_voice_is_the_default_for_every_task(service, repository):
    repository.characters = [skill_character("m", "metadata"), skill_character("g", "core")]

    assert service.character_for_skills(["billing"], ["core", "formatting"], CH, TS).name == "g"


def test_a_character_bound_to_another_always_loaded_skill_is_a_default_too(service, repository):
    repository.characters = [skill_character("f", "formatting")]

    assert service.character_for_skills([], ["core", "formatting"], CH, TS).name == "f"


def test_the_core_skill_is_a_default_even_when_the_always_list_omits_it(service, repository):
    repository.characters = [skill_character("g", "core")]

    assert service.character_for_skills([], [], CH, TS).name == "g"


def test_no_matching_character_means_the_plain_prompt(service, repository, thread_store):
    repository.characters = [skill_character("m", "metadata")]

    assert service.character_for_skills(["jobs"], ["core"], CH, TS) is None
    assert thread_store.names == {}


def test_a_thread_keeps_its_character_when_a_later_question_routes_to_another_skill(service, repository):
    repository.characters = [skill_character("m", "metadata"), skill_character("j", "jobs")]

    first = service.character_for_skills(["metadata"], ["core"], CH, TS)

    assert {service.character_for_skills(["jobs"], ["core"], CH, TS).name for _ in range(10)} == {first.name}


def test_a_stored_character_without_a_persona_is_not_used_for_a_reply(service, repository, thread_store):
    repository.characters = [make_character("reminder-only", persona=""), skill_character("g", "core")]
    thread_store.names[(CH, TS)] = "reminder-only"

    assert service.character_for_skills([], ["core"], CH, TS).name == "g"


def test_a_character_picked_by_a_reminder_keeps_speaking_in_that_thread_when_it_has_a_persona(service, repository, thread_store):
    repository.characters = [make_character("a"), skill_character("g", "core")]
    thread_store.names[(CH, TS)] = "a"

    assert service.character_for_skills([], ["core"], CH, TS).name == "a"


def test_a_character_is_sticky_across_features_and_skills(service, repository):
    repository.characters = [
        make_character("a", bindings=("feature:pr_reminder", "skill:core")),
        make_character("b", bindings=("feature:pr_reminder", "skill:core")),
    ]

    reply = service.character_for_skills([], ["core"], CH, TS)

    assert service.character_for_feature("pr_reminder", CH, TS).name == reply.name


def test_a_reply_never_uses_a_disabled_or_invalid_character(service, repository):
    repository.characters = [skill_character("off", "core", enabled=False), skill_character("bad", "core", persona=""), skill_character("ok", "core")]

    assert {service.character_for_skills([], ["core"], CH, f"{n}.0").name for n in range(20)} == {"ok"}


def test_an_unreadable_table_means_the_plain_prompt(service, repository):
    repository.error = CharacterStorageError("down")

    assert service.character_for_skills([], ["core"], CH, TS) is None


def test_the_picks_are_spread_over_the_candidates(repository, make_service):
    repository.characters = [skill_character(name, "core") for name in "abcd"]
    service = make_service()

    counts = Counter(service.character_for_skills([], ["core"], CH, f"{n}.0").name for n in range(200))

    assert set(counts) == set("abcd")
    assert min(counts.values()) > 20


# --- usable_characters -----------------------------------------------------------------------------


def test_usable_characters_are_the_enabled_valid_ones(service, repository):
    repository.characters = [make_character("ok"), make_character("off", enabled=False), make_character("bad", persona="x" * 2000)]

    assert [c.name for c in service.usable_characters()] == ["ok"]


def test_usable_characters_is_empty_when_the_table_is_unreadable(service, repository):
    repository.error = CharacterStorageError("down")

    assert service.usable_characters() == []


def test_a_feature_pick_with_the_list_in_hand_does_not_read_the_table(service, repository):
    repository.characters = [make_character("a")]
    usable = service.usable_characters()
    reads = repository.reads

    assert service.character_for_feature("pr_reminder", CH, TS, usable).name == "a"
    assert repository.reads == reads


def test_a_bad_row_is_warned_about_once_per_load_not_once_per_thread(service, repository, caplog):
    repository.characters = [make_character("ok"), make_character("bad", persona="x" * 2000)]

    with caplog.at_level(logging.WARNING, logger="characters"):
        usable = service.usable_characters()
        for n in range(5):
            service.character_for_feature("pr_reminder", CH, f"{n}.0", usable)

    assert caplog.text.count("Skipping character bad") == 1


def test_a_pick_with_an_empty_list_in_hand_means_the_default_voice_without_reading_again(service, repository):
    repository.characters = [make_character("a")]
    reads = repository.reads

    assert service.character_for_feature("pr_reminder", CH, TS, []) is None
    assert repository.reads == reads


# --- rows edited by hand ---------------------------------------------------------------------------

MALFORMED = [
    pytest.param({"voices": [1]}, id="voices-array"),
    pytest.param({"voices": "x"}, id="voices-text"),
    pytest.param({"voices": 5}, id="voices-number"),
    pytest.param({"bindings": ("skill:core", None)}, id="binding-null"),
    pytest.param({"bindings": (5,)}, id="binding-number"),
]


def malformed(overrides):
    """A skill-bound row with `overrides` applied, which is how a hand edit leaves it malformed."""
    return make_character("bad", **{"voices": {}, "bindings": ("skill:core",)} | overrides)


@pytest.mark.parametrize("overrides", MALFORMED)
def test_a_malformed_row_is_skipped_and_the_valid_ones_are_still_picked(service, repository, overrides, caplog):
    repository.characters = [malformed(overrides), make_character("good")]

    with caplog.at_level(logging.WARNING, logger="characters"):
        picked = {service.character_for_feature("pr_reminder", CH, f"{n}.0").name for n in range(10)}

    assert picked == {"good"}
    assert "Skipping character bad" in caplog.text


@pytest.mark.parametrize("overrides", MALFORMED)
def test_a_malformed_row_does_not_stop_a_reply_from_getting_a_character(service, repository, overrides):
    repository.characters = [malformed(overrides), skill_character("good", "core")]

    assert service.character_for_skills([], ["core"], CH, TS).name == "good"


@pytest.mark.parametrize("overrides", MALFORMED)
def test_list_characters_reports_a_malformed_row_as_invalid_instead_of_failing(service, repository, overrides):
    repository.characters = [malformed(overrides), make_character("good")]

    overviews = {o.character.name: o for o in service.list_characters()}

    assert overviews["bad"].problems
    assert overviews["good"].problems == ()


# --- list_characters -------------------------------------------------------------------------------


def test_list_characters_reports_problems_and_unknown_skills(service, repository):
    repository.characters = [
        make_character("good", bindings=("feature:pr_reminder", "skill:core")),
        make_character("bad", voices={"pr_reminder": {"headers": ["nothing"]}}),
        skill_character("lost", "ghost", "metadata"),
    ]

    overviews = {o.character.name: o for o in service.list_characters()}

    assert overviews["good"].problems == () and overviews["good"].unknown_skills == ()
    assert overviews["bad"].problems
    assert overviews["lost"].unknown_skills == ("ghost",)


def test_list_characters_includes_disabled_ones(service, repository):
    repository.characters = [make_character("off", enabled=False)]

    assert [o.character.name for o in service.list_characters()] == ["off"]


def test_list_characters_raises_when_the_table_is_unreadable(service, repository):
    repository.error = CharacterStorageError("down")

    with pytest.raises(CharacterStorageError):
        service.list_characters()


# --- seed ------------------------------------------------------------------------------------------


def test_seed_adds_the_missing_characters_and_skips_the_stored_ones(service, repository, source):
    repository.characters = [make_character("a", persona="changed by an admin")]
    source.characters = [make_character("a"), make_character("b")]

    result = service.seed()

    assert (result.added, result.skipped) == (("b",), ("a",))
    assert next(c for c in repository.characters if c.name == "a").persona == "changed by an admin"


def test_seed_never_deletes_a_stored_character(service, repository, source):
    repository.characters = [make_character("mine")]
    source.characters = [make_character("a")]

    service.seed()

    assert {c.name for c in repository.characters} == {"mine", "a"}


def test_seeding_twice_adds_nothing_the_second_time(service, repository, source):
    source.characters = [make_character("a"), make_character("b")]

    service.seed()
    second = service.seed()

    assert (second.added, second.skipped) == ((), ("a", "b"))


def test_seed_refuses_an_empty_source(service, repository):
    with pytest.raises(InvalidSeedError, match="no character files"):
        service.seed()

    assert repository.characters == []


def test_seed_refuses_the_whole_cast_when_one_character_is_invalid_and_lists_every_problem(service, repository, source):
    source.characters = [make_character("good"), make_character("bad1", persona="x" * 2000), make_character("bad2", display_name="")]

    with pytest.raises(InvalidSeedError) as error:
        service.seed()

    assert [p.split(":")[0] for p in error.value.problems] == ["bad1", "bad2"]
    assert repository.characters == []


def test_seed_refuses_a_name_used_by_two_files(service, repository, source):
    source.characters = [make_character("a"), make_character("a")]

    with pytest.raises(InvalidSeedError, match="more than one file"):
        service.seed()

    assert repository.characters == []


def test_seed_lets_a_storage_error_through(service, repository, source):
    source.characters = [make_character("a")]
    repository.error = CharacterStorageError("down")

    with pytest.raises(CharacterStorageError):
        service.seed()


# --- add -------------------------------------------------------------------------------------------


def test_add_stores_a_valid_new_character(service, repository):
    added = service.add(make_character("new"))

    assert [c.name for c in repository.characters] == ["new"]
    assert (added.character.name, added.problems, added.unknown_skills) == ("new", (), ())


def test_add_reports_bindings_to_skills_that_do_not_exist(service, repository):
    added = service.add(skill_character("new", "metadata", "ghost"))

    assert added.unknown_skills == ("ghost",)
    assert [c.name for c in repository.characters] == ["new"]


def test_add_refuses_an_invalid_character_and_stores_nothing(service, repository):
    with pytest.raises(InvalidCharacterError) as error:
        service.add(make_character("new", display_name="", persona="x" * 2000))

    assert len(error.value.problems) == 2
    assert repository.characters == []


def test_add_never_overwrites_an_existing_character(service, repository):
    repository.characters = [make_character("a", persona="the admin's text")]

    with pytest.raises(CharacterExistsError):
        service.add(make_character("a", persona="other"))

    assert repository.characters[0].persona == "the admin's text"


def test_add_needs_at_least_one_binding(service, repository):
    with pytest.raises(InvalidCharacterError, match="needs at least one binding"):
        service.add(make_character("new", bindings=(), voices={}))

    assert repository.characters == []


def test_add_with_dry_run_checks_everything_and_stores_nothing(service, repository):
    added = service.add(make_character("new"), dry_run=True)

    assert added.character.name == "new"
    assert repository.characters == []


def test_add_with_dry_run_still_refuses_an_invalid_character(service, repository):
    with pytest.raises(InvalidCharacterError):
        service.add(make_character("new", persona="x" * 2000), dry_run=True)


def test_add_with_dry_run_still_refuses_a_taken_name(service, repository):
    repository.characters = [make_character("a")]

    with pytest.raises(CharacterExistsError):
        service.add(make_character("a"), dry_run=True)


def test_add_with_dry_run_reports_unknown_skills(service):
    assert service.add(skill_character("new", "ghost"), dry_run=True).unknown_skills == ("ghost",)


def test_add_stores_nothing_when_the_skill_names_cannot_be_read(service, repository, known_skills):
    known_skills.error = CharacterStorageError("skills down")

    with pytest.raises(CharacterStorageError, match="skills down"):
        service.add(make_character("new"))

    assert repository.characters == []


def test_add_lets_a_storage_error_through(service, repository):
    repository.error = CharacterStorageError("down")

    with pytest.raises(CharacterStorageError):
        service.add(make_character("new"))


# --- update ----------------------------------------------------------------------------------------


def stored(repository, **overrides):
    repository.characters = [make_character("a", bindings=("feature:pr_reminder", "skill:core"), song="Old", **overrides)]


def test_update_changes_only_the_fields_given(service, repository):
    stored(repository)

    updated = service.update("a", CharacterChanges(display_name="New Name", enabled=False))

    row = repository.characters[0]
    assert (row.display_name, row.enabled, row.song, row.persona) == ("New Name", False, "Old", "speak softly")
    assert row.bindings == ("feature:pr_reminder", "skill:core")
    assert updated.character == row


def test_update_replaces_the_bindings(service, repository):
    stored(repository)

    service.update("a", CharacterChanges(bindings=("skill:metadata",)))

    assert repository.characters[0].bindings == ("skill:metadata",)


def test_update_adds_and_removes_single_bindings_without_duplicates(service, repository):
    stored(repository)

    service.update("a", CharacterChanges(add_bindings=("skill:metadata", "skill:core"), remove_bindings=("feature:pr_reminder",)))

    assert repository.characters[0].bindings == ("skill:core", "skill:metadata")


def test_update_applies_the_replacement_before_the_additions_and_removals(service, repository):
    stored(repository)

    service.update("a", CharacterChanges(bindings=("skill:jobs",), add_bindings=("skill:metadata",), remove_bindings=("skill:jobs",)))

    assert repository.characters[0].bindings == ("skill:metadata",)


def test_update_replaces_the_voice_block_of_the_feature_it_names_and_keeps_the_others(service, repository):
    repository.characters = [make_character("a", voices={"pr_reminder": {"headers": ["{who} {subject} old"]}})]

    service.update("a", CharacterChanges(voices={"pr_reminder": {"headers": ["{who} {subject} new"]}}))

    assert repository.characters[0].voices == {"pr_reminder": {"headers": ["{who} {subject} new"]}}


@pytest.mark.parametrize("icon", ["icon_emoji", "icon_url"])
def test_update_clears_an_icon_given_as_an_empty_text(service, repository, icon):
    stored(repository, icon_emoji=":star:", icon_url="https://x.test/a.png")

    service.update("a", CharacterChanges(**{icon: ""}))

    assert getattr(repository.characters[0], icon) is None


def test_update_checks_the_character_as_a_whole_and_saves_nothing_when_a_rule_fails(service, repository):
    stored(repository)

    with pytest.raises(InvalidCharacterError) as error:
        service.update("a", CharacterChanges(remove_bindings=("feature:pr_reminder",), persona=" "))

    assert any("persona" in problem for problem in error.value.problems)
    assert repository.characters[0].persona == "speak softly"
    assert repository.characters[0].bindings == ("feature:pr_reminder", "skill:core")


def test_update_refuses_a_binding_that_needs_a_missing_voice(service, repository):
    repository.characters = [skill_character("a", "core")]

    with pytest.raises(InvalidCharacterError, match="needs a voices.pr_reminder block"):
        service.update("a", CharacterChanges(add_bindings=("feature:pr_reminder",)))


def test_update_reports_bindings_to_skills_that_do_not_exist(service, repository):
    stored(repository)

    updated = service.update("a", CharacterChanges(add_bindings=("skill:ghost",)))

    assert updated.unknown_skills == ("ghost",)


def test_update_with_dry_run_checks_everything_and_stores_nothing(service, repository):
    stored(repository)

    updated = service.update("a", CharacterChanges(display_name="New Name", add_bindings=("skill:metadata",)), dry_run=True)

    assert updated.character.display_name == "New Name"
    assert repository.characters[0].display_name == "A"
    assert repository.characters[0].bindings == ("feature:pr_reminder", "skill:core")


def test_update_with_dry_run_still_refuses_an_invalid_result(service, repository):
    stored(repository)

    with pytest.raises(InvalidCharacterError):
        service.update("a", CharacterChanges(persona=" "), dry_run=True)


def test_update_with_dry_run_of_an_unknown_name_is_refused(service):
    with pytest.raises(CharacterNotFoundError):
        service.update("missing", CharacterChanges(enabled=False), dry_run=True)


def test_update_stores_nothing_when_the_skill_names_cannot_be_read(service, repository, known_skills):
    stored(repository)
    known_skills.error = CharacterStorageError("skills down")

    with pytest.raises(CharacterStorageError, match="skills down"):
        service.update("a", CharacterChanges(display_name="New Name"))

    assert repository.characters[0].display_name == "A"


def test_update_of_an_unknown_name_is_refused(service):
    with pytest.raises(CharacterNotFoundError):
        service.update("missing", CharacterChanges(enabled=False))


def test_update_notices_a_character_deleted_while_it_was_being_changed(service, repository):
    stored(repository)
    repository.before_replace = lambda: repository.characters.clear()

    with pytest.raises(CharacterNotFoundError):
        service.update("a", CharacterChanges(enabled=False))


def test_update_refuses_to_overwrite_a_change_made_by_someone_else_in_the_meantime(service, repository):
    stored(repository)
    repository.before_replace = lambda: repository.characters.__setitem__(0, replace(repository.characters[0], persona="their text", updated_at=LATER))

    with pytest.raises(CharacterConflictError):
        service.update("a", CharacterChanges(display_name="Mine"))

    assert (repository.characters[0].persona, repository.characters[0].display_name) == ("their text", "A")


def test_update_passes_the_updated_at_it_read_so_the_store_can_check_it(service, repository):
    repository.characters = [make_character("a", updated_at=EARLIER)]
    seen = []
    original = repository.replace
    repository.replace = lambda character: seen.append(character.updated_at) or original(character)

    service.update("a", CharacterChanges(enabled=False))

    assert seen == [EARLIER]


def test_an_update_can_repair_an_invalid_row(service, repository):
    repository.characters = [make_character("a", voices={"pr_reminder": {"headers": ["no placeholders"]}})]

    service.update("a", CharacterChanges(voices={"pr_reminder": {"headers": ["{who} {subject}"]}}))

    assert [o.problems for o in service.list_characters()] == [()]


def test_changes_that_say_nothing_are_empty():
    assert CharacterChanges().is_empty()
    assert not CharacterChanges(enabled=False).is_empty()
    assert not CharacterChanges(add_bindings=("skill:core",)).is_empty()


# --- remove ----------------------------------------------------------------------------------------


def test_remove_deletes_the_character(service, repository):
    repository.characters = [make_character("a"), make_character("b")]

    removed = service.remove("a")

    assert [c.name for c in repository.characters] == ["b"]
    assert (removed.name, removed.in_seed) == ("a", False)


def test_remove_says_when_the_character_ships_as_a_seed_file(service, repository, source):
    repository.characters = [make_character("a")]
    source.characters = [make_character("a")]

    assert service.remove("a").in_seed is True


def test_remove_still_works_when_the_seed_files_cannot_be_read(service, repository, source):
    repository.characters = [make_character("a")]
    source.read = lambda: (_ for _ in ()).throw(InvalidSeedError(["broken"]))

    assert service.remove("a").in_seed is False
    assert repository.characters == []


def test_remove_of_an_unknown_name_is_refused(service):
    with pytest.raises(CharacterNotFoundError):
        service.remove("missing")


def test_a_thread_that_had_a_removed_character_gets_a_new_one(service, repository, thread_store):
    repository.characters = [make_character("a"), make_character("b")]
    thread_store.names[(CH, TS)] = "a"
    service.remove("a")

    assert service.character_for_feature("pr_reminder", CH, TS).name == "b"
    assert thread_store.names[(CH, TS)] == "b"

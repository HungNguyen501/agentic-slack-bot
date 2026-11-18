from datetime import UTC, datetime
from functools import partial

import psycopg
import pytest

from characters.core.errors import CharacterStorageError
from characters.core.models import Character
from characters.infra.supabase_characters import SupabaseCharacterRepository
from tests.support.characters import make_character
from tests.support.db import fake_connect as shared_fake_connect

repository = SupabaseCharacterRepository()
fake_connect = partial(shared_fake_connect, "characters.infra.supabase_characters.connect")

ROW = {
    "name": "a", "display_name": "A", "song": "S", "artist": "R", "enabled": True, "bindings": ["feature:pr_reminder", "skill:core"],
    "icon_emoji": ":star:", "icon_url": None, "persona": "hi", "voices": {"pr_reminder": {"headers": ["{who} {subject}"]}},
    "updated_at": datetime(2026, 11, 18, 3, 0, tzinfo=UTC),
}  # fmt: skip


def test_list_all_reads_every_column_ordered_by_name():
    with fake_connect(fetchall=[ROW]) as cur:
        repository.list_all()

    sql = cur.execute.call_args.args[0]
    assert sql.startswith("SELECT name, display_name, song, artist, enabled, bindings, icon_emoji, icon_url, persona, voices, updated_at FROM characters")
    assert sql.endswith("ORDER BY name")


def test_a_row_becomes_a_character():
    with fake_connect(fetchall=[ROW]):
        [character] = repository.list_all()

    assert character == Character(
        name="a", display_name="A", bindings=("feature:pr_reminder", "skill:core"), persona="hi",
        voices={"pr_reminder": {"headers": ["{who} {subject}"]}}, song="S", artist="R", enabled=True, icon_emoji=":star:", icon_url=None,
        updated_at=datetime(2026, 11, 18, 3, 0, tzinfo=UTC),
    )  # fmt: skip


def test_null_bindings_and_voices_become_empty():
    with fake_connect(fetchall=[ROW | {"bindings": None, "voices": None}]):
        [character] = repository.list_all()

    assert (character.bindings, dict(character.voices)) == ((), {})


def test_list_all_turns_a_database_error_into_a_storage_error():
    with fake_connect() as cur, pytest.raises(CharacterStorageError, match="Could not read the characters"):
        cur.execute.side_effect = psycopg.OperationalError("down")
        repository.list_all()


def test_add_missing_inserts_each_character_and_leaves_existing_rows_alone():
    with fake_connect() as cur:
        cur.fetchone.side_effect = [{"name": "a"}, None]
        added = repository.add_missing([make_character("a"), make_character("b")])

    sql = cur.execute.call_args_list[0].args[0]
    assert "INSERT INTO characters" in sql and "ON CONFLICT (name) DO NOTHING" in sql and "RETURNING name" in sql
    assert added == ["a"]


def test_add_missing_passes_the_fields_as_parameters():
    character = make_character("a", song="S", artist="R", icon_emoji=":star:", bindings=("skill:core",))
    with fake_connect() as cur:
        cur.fetchone.return_value = {"name": "a"}
        repository.add_missing([character])

    params = cur.execute.call_args.args[1]
    assert params[:9] == ("a", "A", "S", "R", True, ["skill:core"], ":star:", None, "speak softly")
    assert params[9].obj == {"pr_reminder": {"headers": ["{who} {subject}"]}}


def test_add_missing_turns_a_database_error_into_a_storage_error():
    with fake_connect() as cur, pytest.raises(CharacterStorageError, match="Could not store the characters"):
        cur.execute.side_effect = psycopg.OperationalError("down")
        repository.add_missing([make_character("a")])


UPDATED_AT = datetime(2026, 11, 18, 3, 0, tzinfo=UTC)


def test_get_reads_one_character_by_name():
    with fake_connect(fetchone=ROW) as cur:
        character = repository.get("a")

    sql, params = cur.execute.call_args.args
    assert sql.endswith("FROM characters WHERE name = %s") and params == ("a",)
    assert character.name == "a"


def test_get_of_an_unknown_name_is_none():
    with fake_connect(fetchone=None):
        assert repository.get("missing") is None


def test_replace_overwrites_every_field_of_the_row_with_that_name():
    character = make_character("a", song="S", artist="R", icon_emoji=":star:", bindings=("skill:core",), enabled=False, updated_at=UPDATED_AT)
    with fake_connect(rowcount=1) as cur:
        assert repository.replace(character) is True

    sql, params = cur.execute.call_args.args
    assert sql.lstrip().startswith("UPDATE characters SET") and "WHERE name = %s AND updated_at = %s" in sql
    assert params[:8] == ("A", "S", "R", False, ["skill:core"], ":star:", None, "speak softly")
    assert params[8].obj == {"pr_reminder": {"headers": ["{who} {subject}"]}} and params[9] == "a"
    assert params[10] == UPDATED_AT


def test_replace_is_false_when_the_row_is_gone_or_was_changed_since_it_was_read():
    with fake_connect(rowcount=0):
        assert repository.replace(make_character("a", updated_at=UPDATED_AT)) is False


def test_delete_removes_the_row_by_name():
    with fake_connect(rowcount=1) as cur:
        assert repository.delete("a") is True

    assert cur.execute.call_args.args == ("DELETE FROM characters WHERE name = %s", ("a",))


def test_delete_of_an_unknown_name_is_false():
    with fake_connect(rowcount=0):
        assert repository.delete("missing") is False


@pytest.mark.parametrize("call", ["get", "replace", "delete"])
def test_the_other_queries_also_turn_a_database_error_into_a_storage_error(call):
    arguments = {"get": ("a",), "replace": (make_character("a"),), "delete": ("a",)}[call]
    with fake_connect() as cur, pytest.raises(CharacterStorageError):
        cur.execute.side_effect = psycopg.OperationalError("down")
        getattr(repository, call)(*arguments)

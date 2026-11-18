"""V14 must reject rows the read path can't use: `voices` that is not a JSON object, and `bindings` that is not a flat array
of non-null texts. A row edited by hand in Supabase is the only way to get one in.

Runs the real migration file, so it needs a throwaway Postgres: set MIGRATION_TEST_DB_URL to one, e.g.
`docker run --rm -e POSTGRES_PASSWORD=pw -p 54329:5432 postgres:16-alpine` and
`MIGRATION_TEST_DB_URL=postgresql://postgres:pw@127.0.0.1:54329/postgres make test`. Without it the tests are skipped.
Everything happens in one transaction that is rolled back, and the table is created (not dropped), so a database that
already has a `characters` table makes the test error out instead of losing data.
"""
import os
from pathlib import Path

import psycopg
import pytest
from psycopg import errors

MIGRATION = Path(__file__).parents[2] / "src" / "migrations" / "V14__characters.sql"
DB_URL = os.environ.get("MIGRATION_TEST_DB_URL")

pytestmark = pytest.mark.skipif(not DB_URL, reason="needs MIGRATION_TEST_DB_URL pointing at a throwaway Postgres")


@pytest.fixture
def conn():
    """A connection with V14 applied (and the `_set_updated_at` trigger function an earlier migration provides)."""
    with psycopg.connect(DB_URL) as connection:
        connection.execute("create or replace function public._set_updated_at() returns trigger language plpgsql as $$ begin new.updated_at = now(); return new; end $$")
        connection.execute(MIGRATION.read_text())
        yield connection
        connection.rollback()


def insert(conn, bindings="'{}'", voices="'{}'::jsonb", name="a"):
    conn.execute(f"insert into public.characters (name, display_name, bindings, voices) values ('{name}', 'A', {bindings}, {voices})")


def rejected(conn, **columns):
    """Whether the insert is refused by a check constraint; a savepoint keeps the transaction usable afterwards."""
    try:
        with conn.transaction():
            insert(conn, **columns)
    except errors.CheckViolation:
        return True
    return False


@pytest.mark.parametrize("voices", ["'[1]'", "'[]'", "'\"text\"'", "'5'", "'true'", "'null'"], ids=["array", "empty-array", "text", "number", "bool", "json-null"])
def test_voices_that_are_not_a_json_object_are_rejected(conn, voices):
    assert rejected(conn, voices=f"{voices}::jsonb")


@pytest.mark.parametrize("voices", ["'{}'", "'{\"pr_reminder\": {\"headers\": [\"x\"]}}'"], ids=["empty-object", "object"])
def test_voices_that_are_a_json_object_are_accepted(conn, voices):
    assert not rejected(conn, voices=f"{voices}::jsonb")


@pytest.mark.parametrize(
    "bindings",
    ["array['skill:core', null]", "array[null]::text[]", "array[array['a'], array['b']]", "array[array['a', null]]"],
    ids=["null-element", "only-null", "two-dimensional", "nested-with-null"],
)
def test_bindings_with_a_null_element_or_more_than_one_dimension_are_rejected(conn, bindings):
    assert rejected(conn, bindings=bindings)


@pytest.mark.parametrize("bindings", ["'{}'", "array['skill:core']", "array['feature:pr_reminder', 'skill:core']"], ids=["empty", "one", "two"])
def test_bindings_that_are_a_flat_array_of_texts_are_accepted(conn, bindings):
    assert not rejected(conn, bindings=bindings)


def test_the_defaults_satisfy_the_constraints(conn):
    conn.execute("insert into public.characters (name, display_name) values ('d', 'D')")

    assert conn.execute("select bindings, voices, enabled from public.characters where name = 'd'").fetchone() == ([], {}, True)


def test_an_update_cannot_put_a_bad_value_into_an_existing_row(conn):
    insert(conn)

    with pytest.raises(errors.CheckViolation), conn.transaction():
        conn.execute("update public.characters set voices = '[1]'::jsonb where name = 'a'")

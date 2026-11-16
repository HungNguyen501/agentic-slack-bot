"""V12 must keep existing PR reminders inside the old 07:00-21:59 Vietnam-time window (UTC hours 0-14).

Runs the real migration file, so it needs a throwaway Postgres: set MIGRATION_TEST_DB_URL to one, e.g.
`docker run --rm -e POSTGRES_PASSWORD=pw -p 54329:5432 postgres:16-alpine` and
`MIGRATION_TEST_DB_URL=postgresql://postgres:pw@127.0.0.1:54329/postgres make test`. Without it the tests are skipped, like
any test that would need a real service. Everything happens in one transaction that is rolled back, and the table is
created (not dropped), so a database that already has a `schedules` table makes the test error out instead of losing data.
"""
import os
from pathlib import Path

import psycopg
import pytest

MIGRATION = Path(__file__).parents[2] / "src" / "migrations" / "V12__schedules_timezone.sql"
DB_URL = os.environ.get("MIGRATION_TEST_DB_URL")

pytestmark = pytest.mark.skipif(not DB_URL, reason="needs MIGRATION_TEST_DB_URL pointing at a throwaway Postgres")


@pytest.fixture
def migrate():
    """Insert one schedule, run the migration over it, and return its (cron, status, timezone) afterwards."""
    with psycopg.connect(DB_URL) as conn:
        conn.execute(
            "create table public.schedules (id uuid primary key default gen_random_uuid(), cron text not null, "
            "channel text not null default 'C1', question text not null default 'q', bot_id text, "
            "kind text not null default 'question', status text not null default 'active', config jsonb not null default '{}'::jsonb)"
        )

        def run(cron: str, kind: str = "pr_reminder", status: str = "active") -> tuple[str, str, str]:
            conn.execute("insert into public.schedules (id, cron, kind, status) values ('00000000-0000-0000-0000-000000000001', %s, %s, %s)", (cron, kind, status))
            conn.execute(MIGRATION.read_text())
            return conn.execute("select cron, status, timezone from public.schedules").fetchone()

        yield run
        conn.rollback()


@pytest.mark.parametrize(
    "before, after",
    [
        ("*/30 * * * *", "*/30 0-14 * * *"),
        ("0 * * * *", "0 0-14 * * *"),
        ("0 9-23 * * 1-5", "0 9,10,11,12,13,14 * * 1-5"),
        ("0 */2 * * *", "0 0,2,4,6,8,10,12,14 * * *"),
        ("15 0,2,23 * * 1", "15 0,2 * * 1"),
        ("0 22-3 * * *", "0 0,1,2,3 * * *"),
        ("0 10/3 * * *", "0 10,13 * * *"),
    ],
    ids=["every-30m", "hourly", "late-range", "every-2h", "list-with-night-hour", "wraps-midnight", "open-step"],
)
def test_an_existing_reminder_is_cut_down_to_the_old_window(migrate, before, after):
    assert migrate(before) == (after, "active", "UTC")


@pytest.mark.parametrize("cron", ["*/30 0-14 * * 1-5", "0 0-14/7 * * *", "0 3 * * *"], ids=["already-inside", "inside-with-step", "single-hour"])
def test_a_reminder_that_already_fits_the_window_keeps_its_cron(migrate, cron):
    assert migrate(cron) == (cron, "active", "UTC")


@pytest.mark.parametrize("cron", ["0 20,22 * * *", "0 15 * * *"], ids=["only-night-hours", "first-hour-outside"])
def test_a_reminder_that_could_never_fire_in_the_old_window_is_completed(migrate, cron):
    assert migrate(cron) == (cron, "completed", "UTC")


def test_an_unreadable_hour_field_is_restricted_instead_of_left_open(migrate):
    assert migrate("0 x * * *") == ("0 0-14 * * *", "active", "UTC")


@pytest.mark.parametrize(
    "kind, status", [("question", "active"), ("pr_reminder", "completed"), ("dataset_sync", "active")], ids=["question", "ended-reminder", "dataset-sync"]
)
def test_only_active_pr_reminders_are_touched(migrate, kind, status):
    assert migrate("*/30 * * * *", kind=kind, status=status) == ("*/30 * * * *", status, "UTC")

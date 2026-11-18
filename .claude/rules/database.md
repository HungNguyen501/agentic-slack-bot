# Database Access Rules

How code reads and writes Supabase (Postgres). Service boundaries are in [architecture.md](architecture.md); table schemas are in `CLAUDE.md` "Data Models".

## Where the code lives

**New tables follow [layered-architecture.md](layered-architecture.md):** the queries and the row dataclass go in the owning feature's `src/<feature>/infra/`, as the adapter for a port declared in `core/`. The row dataclass is a *persistence model* there: the adapter maps it to a core type (`_row_to_<entity>`) and returns only core types, so rows never reach `core/`. Everything below about dataclass-per-table, `_columns()`, parameterised queries, transactions and migrations still applies to those adapters; only the location, and "callers use the dataclass", change (callers use the core type). The `connectors/db/` entries below describe existing tables until their feature is migrated.

- **`src/connectors/db/<table>.py`** — (existing tables only) the row dataclass and the queries for a table, together: `agent_skills.py` (`bots` and `schedules` have moved to `bots/infra/` and `schedules/infra/`). Do not add new tables here; `connection.py` stays. The dataclass is the data model, so it is defined here and imported by `agent/` (`from connectors.db.agent_skills import AgentSkill`), never redefined or wrapped there. `connection.py` holds `connect()`.
- **What stays in `agent/`:** the logic around the data — validation, what a row means, which row to pick. `agent/skills/sync.py` decides what a valid skill file is; `connectors/db/agent_skills.py` only reads and writes rows.
- **Older task-specific stores** still live under `agent/` and are not being moved: `service_principal_secrets/store.py`, `access_control/access_requests.py` and `access_request_categories.py`. They already follow the dataclass pattern below; when you substantially change one, moving its model to `connectors/db/` is welcome but not required.
- The receiver never touches Supabase (see architecture.md); the worker and scheduler reach it only through these modules.

## Row models

Every table a module reads gets a dataclass for its rows. Never return raw row dicts or tuples out of a DB module.

```python
@dataclass
class AgentSkill:
    name: str
    filename: str
    body: str
    always: bool = False
    description: str = ""

def _columns() -> str:
    return ", ".join(f.name for f in fields(AgentSkill))

def get_all() -> list[AgentSkill]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_columns()} FROM agent_skills ORDER BY filename")
        return [AgentSkill(**row) for row in cur.fetchall()]
```

- One `@dataclass` per table (`frozen=True` when nothing mutates it, as in `DatasetSource`), defined in the `connectors/db/` module that queries it, with required fields first and defaulted ones after.
- Name the dataclass fields after the columns and derive the SELECT / RETURNING list from them in a private `_columns()` function (`fields(...)`), so the column list is written once and every query returns a full row; never `SELECT *`. When a row maps one-to-one onto the fields, build it with `Model(**row)`.
- Only when a value needs converting (`str(uuid)`, `frozenset(...)`, `or []` for a nullable column, a joined or renamed column) write a private `_row_to_<model>(row)` mapper and do that conversion there, so callers never see `None` for a column that has a sensible empty value. `schedules/infra/supabase_schedules.py` and `bots/infra/supabase_bots.py` need one; `agent_skills.py` doesn't.
- Callers use attributes (`skill.name`), never keys. Tests build the dataclass, not a dict.
- A multi-row result that doesn't deserve a model of its own can be a `NamedTuple` (`AddedReminder`).

## Queries

- Open a connection per call with `with connect() as conn, conn.cursor() as cur:` (from `connectors.db.connection`; rows come back as dicts). The `with` block commits, so a multi-statement write is one transaction.
- Pass values as parameters (`%s` / `%(name)s`), never f-string them. Only column lists and fixed fragments may be interpolated.
- Replacing a whole set (skills, dataset snapshots) is one transaction: upsert the new rows, delete the rest, and refuse an empty input rather than wiping the table.
- Handle only errors that can occur at the boundary; let the rest propagate to RQ (see code-style.md). A reader that has nothing to fall back to should raise, not return an empty result that looks like valid data.

## Migrations

- `src/migrations/V<N>__<name>.sql` (Flyway, plain Postgres, lower-case). Pick the next free number. Never edit a migration that has run anywhere — add a new one.
- `create table if not exists public.<table>`, timestamps as `timestamp with time zone`, and the shared `_set_updated_at()` trigger for `updated_at`.
- A column named after a SQL keyword (`name`, `always`, `groups`) must be added to `ignore_words` under `references.keywords` in `.sqlfluff`, or `make lint-sql` fails.
- Per-bot settings are columns on `bots`, never env vars. Reference data shared by every bot is its own table.
- Update the table's entry in `CLAUDE.md` "Data Models" in the same change.

## Tests

- Mirror the module under `tests/` (`src/connectors/db/agent_skills.py` → `tests/connectors/db/test_agent_skills.py`).
- Patch `connect` where the module looks it up (`patch("connectors.db.agent_skills.connect", autospec=True)`) and fake the cursor: assert on the SQL fragment and parameters that matter (scoping, ordering, `WHERE`), and on the dataclass returned. Use the shared `fake_connect` in `tests/support/db.py` (`partial(fake_connect, "<module>.connect")`; see `tests/schedules/infra/test_supabase_schedules.py`).
- A migration that rewrites existing rows gets a real-SQL test in `tests/migrations/` (skipped unless `MIGRATION_TEST_DB_URL` is set).

"""Supabase agent_skills table — the skill files copied into the database."""
from dataclasses import asdict, dataclass, fields

from connectors.db.connection import connect


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
    """Return every stored skill ordered by filename."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_columns()} FROM agent_skills ORDER BY filename COLLATE \"C\"")
        return [AgentSkill(**row) for row in cur.fetchall()]


def replace_all(skills: list[AgentSkill]) -> int:
    """Make the table match `skills` exactly, in one transaction: upsert each skill, delete the rest.

    Args:
        skills: Skills with unique names; must not be empty (that would wipe the table).

    Returns:
        How many rows were deleted because their skill no longer exists.
    """
    if not skills:
        raise ValueError("Refusing to replace agent_skills with an empty list")
    with connect() as conn, conn.cursor() as cur:
        # One INSERT per skill would cross psycopg's auto-prepare threshold, which a transaction-mode pooler can reject.
        conn.prepare_threshold = None
        cur.executemany(
            """
            INSERT INTO agent_skills (name, filename, always, description, body)
            VALUES (%(name)s, %(filename)s, %(always)s, %(description)s, %(body)s)
            ON CONFLICT (name) DO UPDATE SET
                filename = EXCLUDED.filename, always = EXCLUDED.always, description = EXCLUDED.description, body = EXCLUDED.body
            """,
            [asdict(s) for s in skills],
        )
        cur.execute("DELETE FROM agent_skills WHERE name <> ALL(%s)", ([s.name for s in skills],))
        return cur.rowcount

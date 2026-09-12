"""Shared Postgres/Supabase connection helper."""
import psycopg
from psycopg.rows import dict_row

from common.configs import Configs


def connect() -> psycopg.Connection:
    """Open a psycopg3 connection to Supabase that returns rows as dicts."""
    return psycopg.connect(Configs.SUPABASE_DB_URL, row_factory=dict_row)

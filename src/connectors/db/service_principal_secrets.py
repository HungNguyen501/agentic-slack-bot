"""Supabase service_principal_secrets table — cached Databricks SP secret CRUD.

Secrets are stored encrypted (common/crypto.py) and only ever decrypted in-memory by
the caller (worker/agent.py) right before handing them off via a one-time Redis link —
never returned or logged in plaintext from this module.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from connectors.db.connection import connect


@dataclass
class ServicePrincipalSecret:
    id: str
    service_account: str
    client_id: str
    email: str
    dbx_secret_id: str
    secret_hash: str
    secret_encrypted: bytes
    status: str
    dbx_create_time: datetime
    dbx_update_time: datetime
    dbx_expire_time: datetime
    requested_by: str
    created_at: datetime
    updated_at: datetime


def _row_to_secret(row: Any) -> ServicePrincipalSecret:
    return ServicePrincipalSecret(
        id=str(row["id"]),
        service_account=row["service_account"],
        client_id=row["client_id"],
        email=row["email"],
        dbx_secret_id=row["dbx_secret_id"],
        secret_hash=row["secret_hash"],
        secret_encrypted=bytes(row["secret_encrypted"]),
        status=row["status"],
        dbx_create_time=row["dbx_create_time"],
        dbx_update_time=row["dbx_update_time"],
        dbx_expire_time=row["dbx_expire_time"],
        requested_by=row["requested_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def get(email: str) -> ServicePrincipalSecret | None:
    """Fetch the cached secret record for an email, if one exists.

    `email` (the bare address) is the unique lookup key, matching find_service_principal_by_email —
    not `service_account`, which stores Databricks' svc-prefixed display name. Secrets are shared
    across all bots in the workspace, so this isn't scoped by bot_id. Keyed by email rather than
    client_id because a service principal can be deleted and recreated (a new client_id) for the
    same user.
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM service_principal_secrets WHERE email = %s",
            (email,),
        )
        row = cur.fetchone()
        return _row_to_secret(row) if row else None


def upsert(
    service_account: str,
    client_id: str,
    email: str,
    dbx_secret_id: str,
    secret_hash: str,
    secret_encrypted: bytes,
    status: str,
    dbx_create_time: datetime,
    dbx_update_time: datetime,
    dbx_expire_time: datetime,
    requested_by: str,
) -> ServicePrincipalSecret:
    """Insert or replace the cached secret for an email (rotated in place)."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO service_principal_secrets ("
            "service_account, client_id, email, dbx_secret_id, secret_hash, secret_encrypted, "
            "status, dbx_create_time, dbx_update_time, dbx_expire_time, requested_by"
            ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (email) DO UPDATE SET "
            "service_account = EXCLUDED.service_account, client_id = EXCLUDED.client_id, "
            "dbx_secret_id = EXCLUDED.dbx_secret_id, "
            "secret_hash = EXCLUDED.secret_hash, secret_encrypted = EXCLUDED.secret_encrypted, "
            "status = EXCLUDED.status, dbx_create_time = EXCLUDED.dbx_create_time, "
            "dbx_update_time = EXCLUDED.dbx_update_time, dbx_expire_time = EXCLUDED.dbx_expire_time, "
            "requested_by = EXCLUDED.requested_by "
            "RETURNING *",
            (
                service_account,
                client_id,
                email,
                dbx_secret_id,
                secret_hash,
                secret_encrypted,
                status,
                dbx_create_time,
                dbx_update_time,
                dbx_expire_time,
                requested_by,
            ),
        )
        return _row_to_secret(cur.fetchone())

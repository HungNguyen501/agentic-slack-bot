"""Symmetric encryption for values that must be stored at rest but never in plaintext.

Used to cache Databricks service-principal secrets in Supabase (see
connectors/db/service_principal_secrets.py) — Databricks only returns a secret's
plaintext value once, at creation, so honoring "return the existing secret if still
valid" requires storing it ourselves, encrypted.
"""
from cryptography.fernet import Fernet

from common.configs import Configs

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    """Build the Fernet cipher on first use, not at import time.

    SECRET_ENCRYPTION_KEY is optional in Configs like every other env var here — validating
    it eagerly at import time would crash the whole worker process on startup for anyone who
    hasn't set it yet, even if they never touch service-principal secrets.
    """
    global _fernet
    if _fernet is None:
        if not Configs.SECRET_ENCRYPTION_KEY:
            raise RuntimeError("SECRET_ENCRYPTION_KEY is not set")
        _fernet = Fernet(Configs.SECRET_ENCRYPTION_KEY)
    return _fernet


def encrypt(plaintext: str) -> bytes:
    return _get_fernet().encrypt(plaintext.encode())


def decrypt(ciphertext: bytes) -> str:
    return _get_fernet().decrypt(bytes(ciphertext)).decode()

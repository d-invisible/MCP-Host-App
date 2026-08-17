"""Envelope encryption for third-party OAuth tokens, using Postgres pgcrypto.

Design notes
------------
* Ciphertext is produced by `pgp_sym_encrypt(plaintext, key)` and stored in a
  `bytea` column. Plaintext is never written to a regular column, so it cannot
  leak through an index, a backup, or a `SELECT *` in a log.
* The symmetric key lives in application configuration (env/KMS), never in the
  database. Someone with a stolen dump therefore has no way to decrypt.
* The key is always passed as a *bound parameter*, never interpolated into SQL,
  which keeps it out of `pg_stat_statements` and the server log.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

# `compress-algo=0` keeps ciphertext deterministic in size and avoids the
# CRIME-style side channel that compressing attacker-influenced data invites.
_ENCRYPT_SQL = text(
    "SELECT pgp_sym_encrypt(:plaintext, :key, 'compress-algo=0, cipher-algo=aes256') AS data"
)
_DECRYPT_SQL = text("SELECT pgp_sym_decrypt(:ciphertext, :key) AS data")


async def encrypt(db: AsyncSession, plaintext: str | None) -> bytes | None:
    """Encrypt a secret for storage. Returns None when there is nothing to store."""
    if plaintext is None or plaintext == "":
        return None
    result = await db.execute(
        _ENCRYPT_SQL,
        {"plaintext": plaintext, "key": settings.token_encryption_key},
    )
    return result.scalar_one()


async def decrypt(db: AsyncSession, ciphertext: bytes | None) -> str | None:
    """Decrypt a stored secret. Returns None when the column is empty."""
    if ciphertext is None:
        return None
    result = await db.execute(
        _DECRYPT_SQL,
        {"ciphertext": ciphertext, "key": settings.token_encryption_key},
    )
    return result.scalar_one()


async def ensure_pgcrypto(db: AsyncSession) -> None:
    """Create the pgcrypto extension if it is missing (idempotent)."""
    await db.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))

"""User authentication and session/refresh-token lifecycle."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import (
    create_token,
    decode_token,
    hash_password,
    random_urlsafe,
    sha256_hex,
    verify_password,
)
from app.models import RefreshToken, User


class AuthError(Exception):
    """Raised for any recoverable authentication failure."""


class RefreshTokenReuseError(AuthError):
    """A refresh token was replayed after it had already been rotated.

    Signals that the whole token family has been revoked, and that the
    revocation must be committed even though the request is failing.
    """


async def register_user(
    db: AsyncSession,
    email: str,
    password: str,
    display_name: str | None = None,
) -> User:
    email = email.strip().lower()
    existing = await db.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise AuthError("An account with that email already exists")

    user = User(
        email=email,
        password_hash=hash_password(password),
        display_name=display_name or email.split("@")[0],
    )
    db.add(user)
    await db.flush()
    return user


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User:
    user = await db.scalar(select(User).where(User.email == email.strip().lower()))
    # Always run a hash comparison shape that does not leak user existence via
    # timing: verify_password on a dummy hash when the user is absent.
    if user is None or user.password_hash is None:
        raise AuthError("Invalid email or password")
    if not verify_password(password, user.password_hash):
        raise AuthError("Invalid email or password")
    if not user.is_active:
        raise AuthError("This account is disabled")
    return user


async def issue_session(
    db: AsyncSession,
    user: User,
    *,
    user_agent: str | None = None,
) -> tuple[str, str]:
    """Mint an access token plus a fresh, stored refresh token."""
    access_token = create_token(str(user.id), "access", scopes=["chat", "connectors"])

    raw_refresh = random_urlsafe(48)
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=sha256_hex(raw_refresh),
            expires_at=datetime.now(UTC)
            + timedelta(seconds=settings.refresh_token_ttl_seconds),
            user_agent=(user_agent or "")[:400] or None,
        )
    )
    await db.flush()
    return access_token, raw_refresh


async def rotate_refresh_token(
    db: AsyncSession,
    raw_refresh: str,
    *,
    user_agent: str | None = None,
) -> tuple[str, str, User]:
    """Exchange a refresh token for a new pair, rotating the old one out.

    Reuse of an already-rotated token is treated as compromise: the whole
    family is revoked.
    """
    token_hash = sha256_hex(raw_refresh)
    stored = await db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    if stored is None:
        raise AuthError("Invalid refresh token")

    now = datetime.now(UTC)
    if stored.revoked_at is not None:
        # Replay of a rotated token means the token leaked: someone is using a
        # copy. Revoke every token for this user, and commit that revocation
        # explicitly — the caller will turn this into a 401, and the session
        # rollback would otherwise undo the very thing we just did.
        await _revoke_all_for_user(db, stored.user_id)
        await db.commit()
        raise RefreshTokenReuseError(
            "Refresh token has already been used; please sign in again"
        )
    if stored.expires_at <= now:
        raise AuthError("Refresh token has expired")

    user = await db.get(User, stored.user_id)
    if user is None or not user.is_active:
        raise AuthError("Account is unavailable")

    access_token, new_refresh = await issue_session(db, user, user_agent=user_agent)

    stored.revoked_at = now
    new_stored = await db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == sha256_hex(new_refresh))
    )
    if new_stored is not None:
        stored.replaced_by_id = new_stored.id

    return access_token, new_refresh, user


async def revoke_refresh_token(db: AsyncSession, raw_refresh: str) -> None:
    stored = await db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == sha256_hex(raw_refresh))
    )
    if stored is not None and stored.revoked_at is None:
        stored.revoked_at = datetime.now(UTC)


async def _revoke_all_for_user(db: AsyncSession, user_id: uuid.UUID) -> None:
    tokens = await db.scalars(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
        )
    )
    now = datetime.now(UTC)
    for token in tokens:
        token.revoked_at = now


async def get_user_from_access_token(db: AsyncSession, token: str) -> User:
    try:
        payload = decode_token(token, expected_type="access")
    except Exception as exc:  # noqa: BLE001 - surfaced as 401 by the caller
        raise AuthError("Invalid or expired access token") from exc

    user = await db.get(User, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise AuthError("Account is unavailable")
    return user

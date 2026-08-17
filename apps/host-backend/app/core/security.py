"""Password hashing, JWT issuance/verification, and PKCE helpers."""

from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

from app.core.config import settings

_hasher = PasswordHasher()

TokenType = Literal["access", "refresh"]


# --------------------------------------------------------------------------
# passwords
# --------------------------------------------------------------------------
def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False
    return True


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


# --------------------------------------------------------------------------
# our own JWTs (this app's auth server)
# --------------------------------------------------------------------------
def create_token(
    subject: str,
    token_type: TokenType = "access",
    *,
    scopes: list[str] | None = None,
    audience: str | None = None,
    extra_claims: dict[str, Any] | None = None,
    ttl_seconds: int | None = None,
) -> str:
    """Mint a signed JWT.

    `audience` matters for MCP-2 (the "forward auth" server): tokens minted for
    that resource carry its resource URL as `aud`, so the MCP server can reject
    a token that was issued for a different audience.
    """
    now = datetime.now(UTC)
    if ttl_seconds is None:
        ttl_seconds = (
            settings.access_token_ttl_seconds
            if token_type == "access"
            else settings.refresh_token_ttl_seconds
        )

    payload: dict[str, Any] = {
        "sub": subject,
        "iss": settings.issuer_url,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
        "jti": uuid.uuid4().hex,
        "typ": token_type,
    }
    if scopes:
        payload["scope"] = " ".join(scopes)
    if audience:
        payload["aud"] = audience
    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(
    token: str,
    *,
    expected_type: TokenType | None = None,
    audience: str | None = None,
) -> dict[str, Any]:
    """Decode and validate a JWT minted by this app. Raises on any problem."""
    options = {"verify_aud": audience is not None}
    payload: dict[str, Any] = jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
        issuer=settings.issuer_url,
        audience=audience,
        options=options,
    )
    if expected_type is not None and payload.get("typ") != expected_type:
        raise jwt.InvalidTokenError(
            f"expected token type {expected_type!r}, got {payload.get('typ')!r}"
        )
    return payload


# --------------------------------------------------------------------------
# PKCE + random values
# --------------------------------------------------------------------------
def generate_code_verifier(n_bytes: int = 64) -> str:
    return _b64url(secrets.token_bytes(n_bytes))


def code_challenge_from_verifier(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return _b64url(digest)


def verify_code_challenge(verifier: str, challenge: str, method: str = "S256") -> bool:
    if method == "S256":
        return secrets.compare_digest(code_challenge_from_verifier(verifier), challenge)
    if method == "plain":
        return secrets.compare_digest(verifier, challenge)
    return False


def random_urlsafe(n_bytes: int = 32) -> str:
    return secrets.token_urlsafe(n_bytes)


def sha256_hex(value: str) -> str:
    """Hash opaque secrets (auth codes, refresh tokens) before storing them."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

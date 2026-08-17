"""This app's own OAuth 2.1 authorization server.

Used by MCP-2 ("forward auth"), which does not run its own identity provider
and instead treats this host app as its authorization server. Implements the
authorization-code grant with mandatory PKCE and RFC 8707 resource indicators,
so a token minted for one MCP server cannot be replayed against another.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import (
    create_token,
    random_urlsafe,
    sha256_hex,
    verify_code_challenge,
)
from app.models import AuthorizationCode, OAuthClient, User


class OAuthError(Exception):
    """OAuth 2.0 protocol error, rendered as an RFC 6749 error response."""

    def __init__(self, error: str, description: str, status_code: int = 400) -> None:
        super().__init__(description)
        self.error = error
        self.description = description
        self.status_code = status_code


async def get_client(db: AsyncSession, client_id: str) -> OAuthClient:
    client = await db.scalar(select(OAuthClient).where(OAuthClient.client_id == client_id))
    if client is None:
        raise OAuthError("invalid_client", f"Unknown client_id: {client_id}")
    return client


async def register_client(
    db: AsyncSession,
    *,
    client_name: str,
    redirect_uris: list[str],
    scopes: list[str] | None = None,
    grant_types: list[str] | None = None,
) -> tuple[OAuthClient, str | None]:
    """RFC 7591 dynamic client registration.

    Registered clients are public (PKCE, no secret), which is what the MCP spec
    expects for interactive clients.
    """
    if not redirect_uris:
        raise OAuthError("invalid_redirect_uri", "At least one redirect_uri is required")

    client = OAuthClient(
        client_id=f"mcp-client-{random_urlsafe(16)}",
        client_secret_hash=None,
        client_name=client_name,
        redirect_uris=redirect_uris,
        scopes=scopes or ["chat", "profile"],
        grant_types=grant_types or ["authorization_code", "refresh_token"],
        is_cimd=False,
    )
    db.add(client)
    await db.flush()
    return client, None


async def upsert_cimd_client(
    db: AsyncSession,
    *,
    client_id_url: str,
    metadata: dict,
) -> OAuthClient:
    """Register (or refresh) a client identified by a CIMD URL."""
    client = await db.scalar(select(OAuthClient).where(OAuthClient.client_id == client_id_url))
    redirect_uris = metadata.get("redirect_uris") or []
    if not redirect_uris:
        raise OAuthError("invalid_client_metadata", "CIMD document lists no redirect_uris")

    if client is None:
        client = OAuthClient(client_id=client_id_url, is_cimd=True)
        db.add(client)

    client.client_name = metadata.get("client_name", client_id_url)
    client.redirect_uris = redirect_uris
    client.scopes = (metadata.get("scope") or "").split() or ["chat", "profile"]
    client.grant_types = metadata.get("grant_types") or ["authorization_code", "refresh_token"]
    await db.flush()
    return client


def validate_redirect_uri(client: OAuthClient, redirect_uri: str) -> None:
    """Exact-match check. Prefix matching is unsafe and explicitly not used."""
    if redirect_uri not in client.redirect_uris:
        raise OAuthError(
            "invalid_request",
            "redirect_uri does not match any URI registered for this client",
        )


async def create_authorization_code(
    db: AsyncSession,
    *,
    client: OAuthClient,
    user: User,
    redirect_uri: str,
    scopes: list[str],
    code_challenge: str,
    code_challenge_method: str,
    resource: str | None,
) -> str:
    if code_challenge_method != "S256":
        raise OAuthError("invalid_request", "code_challenge_method must be S256")

    raw_code = random_urlsafe(40)
    db.add(
        AuthorizationCode(
            code_hash=sha256_hex(raw_code),
            client_id=client.client_id,
            user_id=user.id,
            redirect_uri=redirect_uri,
            scopes=scopes,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            resource=resource,
            expires_at=datetime.now(UTC) + timedelta(seconds=settings.auth_code_ttl_seconds),
        )
    )
    await db.flush()
    return raw_code


async def exchange_authorization_code(
    db: AsyncSession,
    *,
    code: str,
    client_id: str,
    redirect_uri: str,
    code_verifier: str,
    resource: str | None = None,
) -> dict:
    """Redeem an authorization code for tokens (single use, PKCE enforced)."""
    stored = await db.scalar(
        select(AuthorizationCode).where(AuthorizationCode.code_hash == sha256_hex(code))
    )
    if stored is None:
        raise OAuthError("invalid_grant", "Authorization code is invalid")

    now = datetime.now(UTC)
    if stored.used_at is not None:
        raise OAuthError("invalid_grant", "Authorization code has already been redeemed")
    if stored.expires_at <= now:
        raise OAuthError("invalid_grant", "Authorization code has expired")
    if stored.client_id != client_id:
        raise OAuthError("invalid_grant", "Authorization code was issued to a different client")
    if stored.redirect_uri != redirect_uri:
        raise OAuthError("invalid_grant", "redirect_uri does not match the authorization request")
    if not verify_code_challenge(
        code_verifier, stored.code_challenge, stored.code_challenge_method
    ):
        raise OAuthError("invalid_grant", "PKCE verification failed")

    # A token is bound to the resource requested at authorization time; a
    # mismatch at redemption means the client is trying to widen its audience.
    audience = resource or stored.resource
    if stored.resource is not None and audience != stored.resource:
        raise OAuthError("invalid_target", "resource does not match the authorization request")

    stored.used_at = now

    access_token = create_token(
        str(stored.user_id),
        "access",
        scopes=stored.scopes,
        audience=audience,
        extra_claims={"client_id": client_id},
    )
    refresh_token = create_token(
        str(stored.user_id),
        "refresh",
        scopes=stored.scopes,
        audience=audience,
        extra_claims={"client_id": client_id},
    )

    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": settings.access_token_ttl_seconds,
        "refresh_token": refresh_token,
        "scope": " ".join(stored.scopes),
    }


async def refresh_access_token(
    db: AsyncSession,
    *,
    refresh_token: str,
    client_id: str,
) -> dict:
    from app.core.security import decode_token

    try:
        payload = decode_token(refresh_token, expected_type="refresh")
    except Exception as exc:  # noqa: BLE001
        raise OAuthError("invalid_grant", "Refresh token is invalid or expired") from exc

    if payload.get("client_id") != client_id:
        raise OAuthError("invalid_grant", "Refresh token was issued to a different client")

    user = await db.get(User, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise OAuthError("invalid_grant", "Account is unavailable")

    scopes = (payload.get("scope") or "").split()
    audience = payload.get("aud")

    return {
        "access_token": create_token(
            str(user.id),
            "access",
            scopes=scopes,
            audience=audience,
            extra_claims={"client_id": client_id},
        ),
        "token_type": "Bearer",
        "expires_in": settings.access_token_ttl_seconds,
        "scope": " ".join(scopes),
    }


def build_redirect(redirect_uri: str, params: dict[str, str]) -> str:
    separator = "&" if "?" in redirect_uri else "?"
    return f"{redirect_uri}{separator}{urlencode(params)}"

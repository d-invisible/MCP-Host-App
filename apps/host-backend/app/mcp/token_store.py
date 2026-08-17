"""Database-backed, encrypted token storage for outbound MCP connections.

Implements the MCP SDK's `TokenStorage` protocol so `OAuthClientProvider` can
persist tokens through us instead of holding them in memory. Every secret is
encrypted with pgcrypto before it reaches a column.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MCPConnection, MCPCredential
from app.services import crypto_service


class EncryptedTokenStorage:
    """`TokenStorage` implementation bound to a single MCPConnection row."""

    def __init__(self, db: AsyncSession, connection_id: uuid.UUID) -> None:
        self._db = db
        self._connection_id = connection_id

    async def get_tokens(self) -> OAuthToken | None:
        credential = await self._load()
        if credential is None:
            return None

        access = await crypto_service.decrypt(self._db, credential.access_token_enc)
        if access is None:
            return None
        refresh = await crypto_service.decrypt(self._db, credential.refresh_token_enc)

        expires_in: int | None = None
        if credential.expires_at is not None:
            remaining = (credential.expires_at - datetime.now(UTC)).total_seconds()
            expires_in = max(int(remaining), 0)

        return OAuthToken(
            access_token=access,
            token_type=credential.token_type,
            expires_in=expires_in,
            refresh_token=refresh,
            scope=" ".join(credential.scopes) if credential.scopes else None,
        )

    async def set_tokens(self, tokens: OAuthToken) -> None:
        credential = await self._load()
        if credential is None:
            credential = MCPCredential(connection_id=self._connection_id)
            self._db.add(credential)

        credential.access_token_enc = await crypto_service.encrypt(  # type: ignore[assignment]
            self._db, tokens.access_token
        )
        # An AS may omit the refresh token on refresh; keep the existing one.
        if tokens.refresh_token:
            credential.refresh_token_enc = await crypto_service.encrypt(
                self._db, tokens.refresh_token
            )

        credential.token_type = tokens.token_type or "Bearer"
        credential.scopes = tokens.scope.split() if tokens.scope else []
        credential.expires_at = (
            datetime.now(UTC) + timedelta(seconds=tokens.expires_in)
            if tokens.expires_in is not None
            else None
        )
        credential.refresh_failures = 0
        await self._db.flush()

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        connection = await self._db.get(MCPConnection, self._connection_id)
        if connection is None or not connection.remote_client_id:
            return None

        credential = await self._load()
        secret = (
            await crypto_service.decrypt(self._db, credential.client_secret_enc)
            if credential is not None
            else None
        )

        from pydantic import AnyUrl

        from app.core.config import settings

        return OAuthClientInformationFull(
            client_id=connection.remote_client_id,
            client_secret=secret,
            redirect_uris=[AnyUrl(settings.mcp_oauth_redirect_uri)],
        )

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        connection = await self._db.get(MCPConnection, self._connection_id)
        if connection is None:
            return

        connection.remote_client_id = client_info.client_id
        if client_info.client_secret:
            credential = await self._load()
            if credential is None:
                # No tokens yet: park the secret on a placeholder row so the
                # registration result survives until the code exchange.
                credential = MCPCredential(
                    connection_id=self._connection_id,
                    access_token_enc=b"",
                )
                self._db.add(credential)
            credential.client_secret_enc = await crypto_service.encrypt(
                self._db, client_info.client_secret
            )
        await self._db.flush()

    async def _load(self) -> MCPCredential | None:
        return await self._db.scalar(
            select(MCPCredential).where(MCPCredential.connection_id == self._connection_id)
        )
